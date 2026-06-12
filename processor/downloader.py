import requests
import zipfile
import io
import pandas as pd
import os
import boto3

def transform_gnaf(data_path, statename):
    print(f"Loading {statename}...")
    # --- Load and build full address string ---
    detail   = pd.read_csv(f"{data_path}/{statename}_ADDRESS_DETAIL_psv.psv", sep="|", dtype='str', usecols=['ADDRESS_DETAIL_PID','BUILDING_NAME','LOT_NUMBER',
        'LOT_NUMBER_SUFFIX', 'FLAT_TYPE_CODE', 'FLAT_NUMBER_PREFIX',
        'FLAT_NUMBER', 'FLAT_NUMBER_SUFFIX', 'LEVEL_TYPE_CODE',
        'LEVEL_NUMBER_PREFIX', 'LEVEL_NUMBER', 'LEVEL_NUMBER_SUFFIX',
        'NUMBER_FIRST_PREFIX', 'NUMBER_FIRST', 'NUMBER_FIRST_SUFFIX',
        'NUMBER_LAST_PREFIX', 'NUMBER_LAST', 'NUMBER_LAST_SUFFIX','POSTCODE','STREET_LOCALITY_PID'])
    street     = pd.read_csv(f"{data_path}/{statename}_STREET_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['STREET_LOCALITY_PID','STREET_NAME','STREET_TYPE_CODE','LOCALITY_PID','GNAF_STREET_PID'])
    locality   = pd.read_csv(f"{data_path}/{statename}_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['LOCALITY_PID','LOCALITY_NAME','GNAF_LOCALITY_PID','STATE_PID'])
    state      = pd.read_csv(f"{data_path}/{statename}_STATE_psv.psv", sep="|", dtype='str', usecols=['STATE_PID','STATE_NAME','STATE_ABBREVIATION'])
    geocodedf  = pd.read_csv(f"{data_path}/{statename}_ADDRESS_DEFAULT_GEOCODE_psv.psv", sep="|", dtype='str', usecols=['ADDRESS_DETAIL_PID','LONGITUDE','LATITUDE'])


    dfaddress = detail.merge(
        street.merge(
            locality.merge(
                state, on="STATE_PID", how='left'), on="LOCALITY_PID", how='left'), on="STREET_LOCALITY_PID", how='left')

    # --- Build full address string ---
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
    org_shape = dfaddress.shape
    dfaddress = dfaddress[['ADDRESS_DETAIL_PID', 'full_address']].merge(
        geocodedf, on='ADDRESS_DETAIL_PID', how='left')

    if dfaddress.shape[0] != org_shape[0]:
        print("Warning: Row count mismatch after merging geocoded data!")
    
    return dfaddress


def download_and_process_gnaf(url, month_release, output_path):
    print("Downloading GNAF...")
    r = requests.get(url)
    r.raise_for_status()  # Check if the request was successful
    z = zipfile.ZipFile(io.BytesIO(r.content))
    z.extractall("./tmp/gnaf")
    
    data_path = f"tmp/gnaf/G-NAF/G-NAF {month_release}/Standard"
    
    STATES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT"]
    dfs = [transform_gnaf(data_path, state) for state in STATES]
    
    # Combine and save locally
    print("Combining states...")
    pd.concat(dfs, ignore_index=True).to_parquet(output_path, index=False)
    # Upload to S3
    #print("Uploading to S3...")
    #boto3.client("s3").upload_file(local_path, bucket, key)
    #print(f"Done — uploaded to s3://{bucket}/{key}")
    

#    output_path = "gnaf_addresses.parquet"
if __name__ == "__main__":
    download_and_process_gnaf(
    url = "https://data.gov.au/data/dataset/19432f89-dc3a-4ef3-b943-5326ef1dbecc/resource/f8666213-4079-44da-bede-ebda3a4363e0/download/g-naf_may26_allstates_gda2020_psv_1023.zip",
    month_release = "MAY 2026",
    output_path = "gnaf_addresses.parquet"
#        url=os.environ["GNAF_URL"],
#        month_release=os.environ["GNAF_MONTH_RELEASE"],
        #bucket=os.environ["GNAF_BUCKET"],
    #    key="gnaf_addresses.parquet"
    )
    