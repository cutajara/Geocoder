import os
import sys
import zipfile
import requests
import boto3
import pandas as pd
from opensearchpy import OpenSearch, RequestsHttpConnection, helpers
from requests_aws4auth import AWS4Auth

# Grab environment variables passed down by our CDK Stack
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT")
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
GNAF_URL = os.environ.get("GNAF_URL")
GNAF_RELEASE = os.environ.get("GNAF_RELEASE") # e.g., "MAY 2026"

def get_opensearch_client():
    """Initializes and returns an authenticated SigV4 OpenSearch Client"""
    credentials = boto3.Session().get_credentials()
    awsauth = AWS4Auth(
        credentials.access_key, credentials.secret_key, 
        AWS_REGION, 'es', session_token=credentials.token
    )
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_ENDPOINT, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection
    )

def stream_download_zip(url, target_zip_path):
    """Downloads a file using stream=True so large zips never flood container RAM"""
    print(f"Streaming download from data.gov.au to {target_zip_path}...")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(target_zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    print("Download complete!")

def process_and_bulk_index_state(client, data_path, statename):
    """Transforms state rows in streaming chunks to keep memory usage flat"""
    print(f"Processing and indexing {statename}...")
    
    detail_file = f"{data_path}/{statename}_ADDRESS_DETAIL_psv.psv"
    if not os.path.exists(detail_file):
        print(f"Skipping {statename} (file not found).")
        return

    # 1. Load small lookup tables fully into memory (Safe, they are small)
    street = pd.read_csv(f"{data_path}/{statename}_STREET_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['STREET_LOCALITY_PID','STREET_NAME','STREET_TYPE_CODE','LOCALITY_PID'])
    locality = pd.read_csv(f"{data_path}/{statename}_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['LOCALITY_PID','LOCALITY_NAME','STATE_PID'])
    state = pd.read_csv(f"{data_path}/{statename}_STATE_psv.psv", sep="|", dtype='str', usecols=['STATE_PID','STATE_ABBREVIATION'])
    geocodedf = pd.read_csv(f"{data_path}/{statename}_ADDRESS_DEFAULT_GEOCODE_psv.psv", sep="|", dtype='str', usecols=['ADDRESS_DETAIL_PID','LONGITUDE','LATITUDE'])

    # 2. Pre-merge the static lookup metadata to minimize merge footprint inside the loop
    meta_lookup = street.merge(locality.merge(state, on="STATE_PID", how='left'), on="LOCALITY_PID", how='left')
    del street, locality, state # Free lookup memory instantly

    # 3. Stream the massive address detail file in small chunks of 50,000 rows
    detail_cols = [
        'ADDRESS_DETAIL_PID','BUILDING_NAME','LOT_NUMBER','LOT_NUMBER_SUFFIX', 
        'FLAT_TYPE_CODE', 'FLAT_NUMBER_PREFIX', 'FLAT_NUMBER', 'FLAT_NUMBER_SUFFIX', 
        'LEVEL_TYPE_CODE', 'LEVEL_NUMBER_PREFIX', 'LEVEL_NUMBER', 'LEVEL_NUMBER_SUFFIX',
        'NUMBER_FIRST_PREFIX', 'NUMBER_FIRST', 'NUMBER_FIRST_SUFFIX',
        'NUMBER_LAST_PREFIX', 'NUMBER_LAST', 'NUMBER_LAST_SUFFIX','POSTCODE','STREET_LOCALITY_PID'
    ]
    
    chunk_size = 10000
    actions = []
    
    # Passing chunksize makes pd.read_csv stream rows sequentially instead of flooding RAM
    for detail_chunk in pd.read_csv(detail_file, sep="|", dtype='str', usecols=detail_cols, chunksize=chunk_size):
        
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

        # Build actions loop for this chunk slice
        for _, row in df_final.iterrows():
            try:
                lat = float(row['LATITUDE']) if pd.notna(row['LATITUDE']) else None
                lon = float(row['LONGITUDE']) if pd.notna(row['LONGITUDE']) else None
            except ValueError:
                lat, lon = None, None

            actions.append({
                "_index": "gnaf",
                "_id": row['ADDRESS_DETAIL_PID'],
                "_source": {
                    "gnaf_pid": row['ADDRESS_DETAIL_PID'],
                    "full_address": row['full_address'],
                    "latitude": lat,
                    "longitude": lon
                }
            })

            # Send batch payloads continuously to OpenSearch
            if len(actions) >= 5000:
                helpers.bulk(client, actions)
                actions = []

    # Upload any leftover records across chunks
    if actions:
        helpers.bulk(client, actions)

    # Clean up static lookup arrays entirely before advancing to the next state
    del meta_lookup, geocodedf
    print(f"Finished indexing {statename} successfully!")

def run_pipeline():
    """Main execution orchestrator triggered by entrypoint.py inside Fargate"""
    if not OPENSEARCH_ENDPOINT or not GNAF_URL or not GNAF_RELEASE:
        print("Error: Missing vital configuration variables. Exiting.")
        sys.exit(1)

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
        print("Created clean 'gnaf' OpenSearch index.")

    # Step 2: Stream archive zip package onto local disk
    os.makedirs("./tmp", exist_ok=True)
    zip_file_path = "./tmp/gnaf_data.zip"
    stream_download_zip(GNAF_URL, zip_file_path)

    # Step 3: Unzip archive
    print("Unpacking GNAF archive...")
    with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
        zip_ref.extractall("./tmp/extracted")
    
    # Path inside the extracted bundle
    data_path = f"./tmp/extracted/G-NAF/G-NAF {GNAF_RELEASE}/Standard"
    
    # Step 4: Stream each state sequentially
    STATES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT"]
    #STATES = ["OT"]
    for state in STATES:
        process_and_bulk_index_state(client, data_path, state)

    print("Success! All 13 Million GNAF records are safely populated in your Serverless Cluster.")

if __name__ == "__main__":
    run_pipeline()
