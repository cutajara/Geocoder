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
    """Transforms a single state and streams batches directly into OpenSearch"""
    print(f"Processing and indexing {statename}...")
    
    # Check if the mandatory state file exists before processing
    detail_file = f"{data_path}/{statename}_ADDRESS_DETAIL_psv.psv"
    if not os.path.exists(detail_file):
        print(f"Skipping {statename} (file not found).")
        return

    # Load and merge dataframes exactly like your original clean code
    detail = pd.read_csv(detail_file, sep="|", dtype='str', usecols=[
        'ADDRESS_DETAIL_PID','BUILDING_NAME','LOT_NUMBER','LOT_NUMBER_SUFFIX', 
        'FLAT_TYPE_CODE', 'FLAT_NUMBER_PREFIX', 'FLAT_NUMBER', 'FLAT_NUMBER_SUFFIX', 
        'LEVEL_TYPE_CODE', 'LEVEL_NUMBER_PREFIX', 'LEVEL_NUMBER', 'LEVEL_NUMBER_SUFFIX',
        'NUMBER_FIRST_PREFIX', 'NUMBER_FIRST', 'NUMBER_FIRST_SUFFIX',
        'NUMBER_LAST_PREFIX', 'NUMBER_LAST', 'NUMBER_LAST_SUFFIX','POSTCODE','STREET_LOCALITY_PID'
    ])
    
    street = pd.read_csv(f"{data_path}/{statename}_STREET_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['STREET_LOCALITY_PID','STREET_NAME','STREET_TYPE_CODE','LOCALITY_PID'])
    locality = pd.read_csv(f"{data_path}/{statename}_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['LOCALITY_PID','LOCALITY_NAME','STATE_PID'])
    state = pd.read_csv(f"{data_path}/{statename}_STATE_psv.psv", sep="|", dtype='str', usecols=['STATE_PID','STATE_ABBREVIATION'])
    geocodedf = pd.read_csv(f"{data_path}/{statename}_ADDRESS_DEFAULT_GEOCODE_psv.psv", sep="|", dtype='str', usecols=['ADDRESS_DETAIL_PID','LONGITUDE','LATITUDE'])

    dfaddress = detail.merge(
        street.merge(
            locality.merge(state, on="STATE_PID", how='left'), 
            on="LOCALITY_PID", how='left'), 
        on="STREET_LOCALITY_PID", how='left'
    )

    # Reassemble full address string matching your clean mapping logic
    dfaddress["full_address"] = (
        dfaddress["BUILDING_NAME"].fillna("").astype(str) + " " +
        dfaddress["LOT_NUMBER"].fillna("").astype(str) + " " +
        dfaddress["LOT_NUMBER_SUFFIX"].fillna("").astype(str) + " " +
        dfaddress["FLAT_TYPE_CODE"].fillna("").astype(str) + " " +
        dfaddress["FLAT_NUMBER_PREFIX"].fillna("").astype(str) + " " +
        dfaddress["FLAT_NUMBER"].fillna("").astype(str) + " " +
        dfaddress["FLAT_NUMBER_SUFFIX"].fillna("").astype(str) + " " +
        dfaddress["LEVEL_TYPE_CODE"].fillna("").astype(str) + " " +
        dfaddress["LEVEL_NUMBER_PREFIX"].fillna("").astype(str) + " " +
        dfaddress["LEVEL_NUMBER"].fillna("").astype(str) + " " +
        dfaddress["LEVEL_NUMBER_SUFFIX"].fillna("").astype(str) + " " +
        dfaddress["NUMBER_FIRST_PREFIX"].fillna("").astype(str) + " " +
        dfaddress["NUMBER_FIRST"].fillna("").astype(str) + " " +
        dfaddress["NUMBER_FIRST_SUFFIX"].fillna("").astype(str) + " " +
        dfaddress["NUMBER_LAST_PREFIX"].fillna("").astype(str) + " " +
        dfaddress["NUMBER_LAST"].fillna("").astype(str) + " " +
        dfaddress["NUMBER_LAST_SUFFIX"].fillna("").astype(str) + " " +
        dfaddress["STREET_NAME"].fillna("").astype(str) + " " +
        dfaddress["STREET_TYPE_CODE"].fillna("").astype(str) + " " +
        dfaddress["LOCALITY_NAME"].fillna("").astype(str) + " " +
        dfaddress["STATE_ABBREVIATION"].fillna("").astype(str) + " " +
        dfaddress["POSTCODE"].fillna("").astype(str)
    )
    dfaddress["full_address"] = dfaddress["full_address"].str.replace(r'\s+', ' ', regex=True).str.strip()
    
    # Final merge with coordinates
    df_final = dfaddress[['ADDRESS_DETAIL_PID', 'full_address']].merge(geocodedf, on='ADDRESS_DETAIL_PID', how='left')
    
    # Clear out the large dataframes right away to conserve memory before indexing
    del detail, street, locality, state, geocodedf, dfaddress

    # Chunk the final state data into batches of 5000 records to pipe to OpenSearch
    batch_size = 5000
    actions = []
    
    for idx, row in df_final.iterrows():
        # Handle nan coordinates gracefully so database properties don't reject them
        try:
            lat = float(row['LATITUDE']) if pd.notna(row['LATITUDE']) else None
            lon = float(row['LONGITUDE']) if pd.notna(row['LONGITUDE']) else None
        except ValueError:
            lat, lon = None, None

        # Build bulk action payload format
        action = {
            "_index": "gnaf",
            "_id": row['ADDRESS_DETAIL_PID'],
            "_source": {
                "gnaf_pid": row['ADDRESS_DETAIL_PID'],
                "full_address": row['full_address'],
                "latitude": lat,
                "longitude": lon
            }
        }
        actions.append(action)

        # Trigger network transmission when batch capacity is reached
        if len(actions) >= batch_size:
            helpers.bulk(client, actions)
            actions = [] # Clear out the uploaded batch chunk from container RAM

    # Empty any remainder records left in the list
    if actions:
        helpers.bulk(client, actions)
        
    print(f"Finished indexing {statename} successfully!")
    del df_final # Free up the remaining state memory block

def run_pipeline():
    """Main execution orchestrator triggered by entrypoint.py inside Fargate"""
    if not OPENSEARCH_ENDPOINT or not GNAF_URL or not GNAF_RELEASE:
        print("Error: Missing vital configuration variables. Exiting.")
        sys.exit(1)

    client = get_opensearch_client()
    
    # Step 1: Create GNAF Index Schema structure if it doesn't exist
    index_config = {
        "settings": {
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
    #STATES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT"]
    STATES = ["OT"]
    for state in STATES:
        process_and_bulk_index_state(client, data_path, state)

    print("Success! All 13 Million GNAF records are safely populated in your Serverless Cluster.")

if __name__ == "__main__":
    run_pipeline()
