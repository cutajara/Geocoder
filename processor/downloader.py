import os
import sys
import zipfile
import shutil
import traceback
import gc
from datetime import datetime
import requests
import boto3
import pandas as pd
from opensearchpy import OpenSearch, RequestsHttpConnection, helpers
from requests_aws4auth import AWS4Auth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Grab environment variables passed down by our CDK Stack
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT")
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
GNAF_URL = os.environ.get("GNAF_URL")
GNAF_RELEASE = os.environ.get("GNAF_RELEASE") # e.g., "MAY 2026"

CHUNK_SIZE = int(os.environ.get("GNAF_CHUNK_SIZE", "5000"))
BULK_CHUNK_SIZE = int(os.environ.get("GNAF_BULK_CHUNK_SIZE", "1000"))
REQUEST_TIMEOUT_SEC = int(os.environ.get("GNAF_REQUEST_TIMEOUT_SEC", "120"))
BULK_REQUEST_TIMEOUT_SEC = int(os.environ.get("GNAF_BULK_REQUEST_TIMEOUT_SEC", "90"))
CONTINUE_ON_STATE_ERROR = os.environ.get("CONTINUE_ON_STATE_ERROR", "false").lower() == "true"


def log(message):
    """Print log lines with UTC timestamp and immediate flush for CloudWatch."""
    print(f"[{datetime.utcnow().isoformat()}Z] {message}", flush=True)


def log_disk_usage(path):
    total, used, free = shutil.disk_usage(path)
    gib = 1024 ** 3
    log(
        f"Disk usage for {path} -> total={total / gib:.2f} GiB, "
        f"used={used / gib:.2f} GiB, free={free / gib:.2f} GiB"
    )

def get_opensearch_client():
    """Initializes and returns an authenticated SigV4 OpenSearch Client"""
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("Unable to obtain AWS credentials from boto3 session")

    awsauth = AWS4Auth(
        credentials.access_key, credentials.secret_key, 
        AWS_REGION, 'es', session_token=credentials.token
    )
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_ENDPOINT, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=60,
        max_retries=5,
        retry_on_timeout=True
    )

def stream_download_zip(url, target_zip_path):
    """Downloads a file using stream=True so large zips never flood container RAM"""
    log(f"Streaming download from data.gov.au to {target_zip_path}...")

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))

    bytes_written = 0
    report_every_bytes = 256 * 1024 * 1024
    next_report = report_every_bytes

    with session.get(url, stream=True, timeout=(10, REQUEST_TIMEOUT_SEC)) as r:
        r.raise_for_status()
        content_length = r.headers.get("Content-Length")
        if content_length:
            log(f"Expected download size: {int(content_length) / (1024 ** 3):.2f} GiB")

        with open(target_zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    bytes_written += len(chunk)
                    if bytes_written >= next_report:
                        log(f"Downloaded {bytes_written / (1024 ** 3):.2f} GiB...")
                        next_report += report_every_bytes

    log(f"Download complete ({bytes_written / (1024 ** 3):.2f} GiB written)")


def extract_state_files(zip_file_path, extract_root, gnaf_release, statename):
    """Extract only files needed for a single state to keep disk usage bounded."""
    standard_prefix = f"G-NAF/G-NAF {gnaf_release}/Standard"
    needed_files = [
        f"{statename}_ADDRESS_DETAIL_psv.psv",
        f"{statename}_STREET_LOCALITY_psv.psv",
        f"{statename}_LOCALITY_psv.psv",
        f"{statename}_STATE_psv.psv",
        f"{statename}_ADDRESS_DEFAULT_GEOCODE_psv.psv",
    ]

    log(f"Extracting state files for {statename}...")
    with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
        zip_names = set(zip_ref.namelist())
        for filename in needed_files:
            member = f"{standard_prefix}/{filename}"
            if member not in zip_names:
                log(f"Missing expected file in archive: {member}")
                continue
            zip_ref.extract(member, extract_root)

    return os.path.join(extract_root, "G-NAF", f"G-NAF {gnaf_release}", "Standard")


def cleanup_state_files(data_path, statename):
    """Delete extracted state files after indexing to reclaim ephemeral storage."""
    suffixes = [
        "ADDRESS_DETAIL_psv.psv",
        "STREET_LOCALITY_psv.psv",
        "LOCALITY_psv.psv",
        "STATE_psv.psv",
        "ADDRESS_DEFAULT_GEOCODE_psv.psv",
    ]
    for suffix in suffixes:
        fp = os.path.join(data_path, f"{statename}_{suffix}")
        if os.path.exists(fp):
            os.remove(fp)


def log_memory_hint():
    """Best-effort memory visibility without adding native dependencies."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as proc_status:
            for line in proc_status:
                if line.startswith(("VmRSS:", "VmHWM:", "VmSize:")):
                    log(f"Memory {line.strip()}")
    except OSError:
        pass


def bulk_index_actions(client, actions, statename, chunk_number):
    """Bulk index with retries and non-fatal reporting of per-document failures."""
    success_count = 0
    failure_count = 0

    for ok, item in helpers.streaming_bulk(
        client,
        actions,
        chunk_size=BULK_CHUNK_SIZE,
        request_timeout=BULK_REQUEST_TIMEOUT_SEC,
        raise_on_error=False,
        raise_on_exception=False,
        max_retries=3,
        initial_backoff=2,
        max_backoff=30,
    ):
        if ok:
            success_count += 1
        else:
            failure_count += 1
            if failure_count <= 3:
                log(f"Bulk error sample ({statename} chunk {chunk_number}): {item}")

    if failure_count > 0:
        log(
            f"Bulk completed with partial failures for {statename} chunk {chunk_number}: "
            f"success={success_count}, failed={failure_count}"
        )

    return success_count, failure_count

def process_and_bulk_index_state(client, data_path, statename):
    """Transforms state rows in streaming chunks to keep memory usage flat"""
    log(f"Processing and indexing {statename}...")
    log_memory_hint()
    
    detail_file = f"{data_path}/{statename}_ADDRESS_DETAIL_psv.psv"
    if not os.path.exists(detail_file):
        log(f"Skipping {statename} (detail file not found).")
        return

    # 1. Load small lookup tables fully into memory (Safe, they are small)
    street = pd.read_csv(f"{data_path}/{statename}_STREET_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['STREET_LOCALITY_PID','STREET_NAME','STREET_TYPE_CODE','LOCALITY_PID'])
    locality = pd.read_csv(f"{data_path}/{statename}_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['LOCALITY_PID','LOCALITY_NAME','STATE_PID'])
    state = pd.read_csv(f"{data_path}/{statename}_STATE_psv.psv", sep="|", dtype='str', usecols=['STATE_PID','STATE_ABBREVIATION'])
    geocodedf = pd.read_csv(f"{data_path}/{statename}_ADDRESS_DEFAULT_GEOCODE_psv.psv", sep="|", dtype='str', usecols=['ADDRESS_DETAIL_PID','LONGITUDE','LATITUDE'])

    # 2. Pre-merge the static lookup metadata to minimize merge footprint inside the loop
    meta_lookup = street.merge(locality.merge(state, on="STATE_PID", how='left'), on="LOCALITY_PID", how='left')
    del street, locality, state # Free lookup memory instantly

    # 3. Stream the massive address detail file in smaller chunks
    detail_cols = [
        'ADDRESS_DETAIL_PID','BUILDING_NAME','LOT_NUMBER','LOT_NUMBER_SUFFIX', 
        'FLAT_TYPE_CODE', 'FLAT_NUMBER_PREFIX', 'FLAT_NUMBER', 'FLAT_NUMBER_SUFFIX', 
        'LEVEL_TYPE_CODE', 'LEVEL_NUMBER_PREFIX', 'LEVEL_NUMBER', 'LEVEL_NUMBER_SUFFIX',
        'NUMBER_FIRST_PREFIX', 'NUMBER_FIRST', 'NUMBER_FIRST_SUFFIX',
        'NUMBER_LAST_PREFIX', 'NUMBER_LAST', 'NUMBER_LAST_SUFFIX','POSTCODE','STREET_LOCALITY_PID'
    ]
    
    total_success = 0
    total_failures = 0
    total_rows = 0
    
    # Passing chunksize makes pd.read_csv stream rows sequentially instead of flooding RAM
    for chunk_number, detail_chunk in enumerate(
        pd.read_csv(detail_file, sep="|", dtype='str', usecols=detail_cols, chunksize=CHUNK_SIZE),
        start=1,
    ):
        
        # Merge this minor 10k slice with your lookups
        dfaddress = detail_chunk.merge(meta_lookup, on="STREET_LOCALITY_PID", how='left')
        df_final = dfaddress.merge(geocodedf, on='ADDRESS_DETAIL_PID', how='left')

        # Reassemble address strings for this chunk slice
        full_addresses = (
            df_final["BUILDING_NAME"].fillna("").astype(str) + " " +
            df_final["LOT_NUMBER"].fillna("").astype(str) + " " +
            df_final["LOT_NUMBER_SUFFIX"].fillna("").astype(str) + " " +
            df_final["FLAT_TYPE_CODE"].fillna("").astype(str) + " " +
            df_final["FLAT_NUMBER_PREFIX"].fillna("").astype(str) + " " +
            df_final["FLAT_NUMBER"].fillna("").astype(str) + " " +
            df_final["FLAT_NUMBER_SUFFIX"].fillna("").astype(str) + " " +
            df_final["LEVEL_TYPE_CODE"].fillna("").astype(str) + " " +
            df_final["LEVEL_NUMBER_PREFIX"].fillna("").astype(str) + " " +
            df_final["LEVEL_NUMBER"].fillna("").astype(str) + " " +
            df_final["LEVEL_NUMBER_SUFFIX"].fillna("").astype(str) + " " +
            df_final["NUMBER_FIRST_PREFIX"].fillna("").astype(str) + " " +
            df_final["NUMBER_FIRST"].fillna("").astype(str) + " " +
            df_final["NUMBER_FIRST_SUFFIX"].fillna("").astype(str) + " " +
            df_final["NUMBER_LAST_PREFIX"].fillna("").astype(str) + " " +
            df_final["NUMBER_LAST"].fillna("").astype(str) + " " +
            df_final["NUMBER_LAST_SUFFIX"].fillna("").astype(str) + " " +
            df_final["STREET_NAME"].fillna("").astype(str) + " " +
            df_final["STREET_TYPE_CODE"].fillna("").astype(str) + " " +
            df_final["LOCALITY_NAME"].fillna("").astype(str) + " " +
            df_final["STATE_ABBREVIATION"].fillna("").astype(str) + " " +
            df_final["POSTCODE"].fillna("").astype(str)
        )
        df_final["full_address"] = full_addresses.str.replace(r'\s+', ' ', regex=True).str.strip()

        def action_iter(df_final):
            for row in df_final.itertuples(index=False):
                try:
                    lat = float(row.LATITUDE) if pd.notna(row.LATITUDE) else None
                    lon = float(row.LONGITUDE) if pd.notna(row.LONGITUDE) else None
                except ValueError:
                    lat, lon = None, None

                yield {
                    "_index": "gnaf",
                    "_id": row.ADDRESS_DETAIL_PID,
                    "_source": {
                        "gnaf_pid": row.ADDRESS_DETAIL_PID,
                        "full_address": row.full_address,
                        "latitude": lat,
                        "longitude": lon,
                    },
                }

        success_count, failure_count = bulk_index_actions(client, action_iter(df_final), statename, chunk_number)
        total_success += success_count
        total_failures += failure_count
        total_rows += len(df_final)

        if chunk_number % 10 == 0:
            log(
                f"{statename} progress: chunks={chunk_number}, rows_seen={total_rows}, "
                f"indexed={total_success}, failed={total_failures}"
            )
            log_memory_hint()

        del detail_chunk, dfaddress, df_final, full_addresses
        gc.collect()

    # Clean up static lookup arrays entirely before advancing to the next state
    del meta_lookup, geocodedf
    gc.collect()
    log_memory_hint()

    log(
        f"Finished indexing {statename}. rows_seen={total_rows}, "
        f"indexed={total_success}, failed={total_failures}"
    )

def run_pipeline():
    """Main execution orchestrator triggered by entrypoint.py inside Fargate"""
    try:
        if not OPENSEARCH_ENDPOINT or not GNAF_URL or not GNAF_RELEASE:
            log("Error: Missing vital configuration variables. Exiting.")
            sys.exit(1)

        log(
            f"Starting pipeline with region={AWS_REGION}, chunk_size={CHUNK_SIZE}, "
            f"bulk_chunk_size={BULK_CHUNK_SIZE}, continue_on_state_error={CONTINUE_ON_STATE_ERROR}"
        )

        client = get_opensearch_client()

        # Step 1: Create GNAF Index Schema structure if it doesn't exist
        index_config = {
            "settings": {
                "index": {
                    "number_of_replicas": 0 
                },
                "analysis": {
                    "analyzer": {
                        "address_analyzer": {
                            "tokenizer": "standard",
                            "filter": ["lowercase"]
                        }
                    }
                }
            },
            "mappings": {
                "properties": {
                    "gnaf_pid":      {"type": "keyword"},
                    "full_address":  {"type": "text", "analyzer": "address_analyzer"},
                    "latitude":      {"type": "float"},
                    "longitude":     {"type": "float"}
                }
            }
        }

        if not client.indices.exists(index="gnaf"):
            client.indices.create(index="gnaf", body=index_config)
            log("Created clean 'gnaf' OpenSearch index.")

        # Step 2: Stream archive zip package onto local disk
        os.makedirs("./tmp", exist_ok=True)
        extract_root = "./tmp/extracted"
        os.makedirs(extract_root, exist_ok=True)

        log_disk_usage("./tmp")
        zip_file_path = "./tmp/gnaf_data.zip"
        stream_download_zip(GNAF_URL, zip_file_path)
        log_disk_usage("./tmp")

        # Step 3: Stream each state sequentially (extract only required files per state)
        states_env = os.environ.get("GNAF_STATES")
        states = [s.strip().upper() for s in states_env.split(",")] if states_env else ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT"]

        for state in states:
            try:
                data_path = extract_state_files(zip_file_path, extract_root, GNAF_RELEASE, state)
                process_and_bulk_index_state(client, data_path, state)
            except Exception:
                log(f"State processing failed for {state}")
                log(traceback.format_exc())
                if not CONTINUE_ON_STATE_ERROR:
                    raise
            finally:
                cleanup_state_files(os.path.join(extract_root, "G-NAF", f"G-NAF {GNAF_RELEASE}", "Standard"), state)
                gc.collect()
                log_disk_usage("./tmp")
                log_memory_hint()

        log("Success! All configured GNAF states have been processed.")
    except Exception:
        log("Fatal pipeline error")
        log(traceback.format_exc())
        raise

if __name__ == "__main__":
    run_pipeline()
