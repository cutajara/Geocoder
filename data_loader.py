
import pandas as pd
from rank_bm25 import BM25Okapi
from geocoder import sanitize_input_string
import boto3
import io

def load_gnaf(data_path: str):

    print("Loading GNAF data...")
    # --- Load and build full address string ---
    detail   = pd.read_csv(f"{data_path}/VIC_ADDRESS_DETAIL_psv.psv", sep="|", dtype='str', usecols=['ADDRESS_DETAIL_PID','BUILDING_NAME','LOT_NUMBER',
        'LOT_NUMBER_SUFFIX', 'FLAT_TYPE_CODE', 'FLAT_NUMBER_PREFIX',
        'FLAT_NUMBER', 'FLAT_NUMBER_SUFFIX', 'LEVEL_TYPE_CODE',
        'LEVEL_NUMBER_PREFIX', 'LEVEL_NUMBER', 'LEVEL_NUMBER_SUFFIX',
        'NUMBER_FIRST_PREFIX', 'NUMBER_FIRST', 'NUMBER_FIRST_SUFFIX',
        'NUMBER_LAST_PREFIX', 'NUMBER_LAST', 'NUMBER_LAST_SUFFIX','POSTCODE','STREET_LOCALITY_PID'])
    street   = pd.read_csv(f"{data_path}/VIC_STREET_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['STREET_LOCALITY_PID','STREET_NAME','STREET_TYPE_CODE','LOCALITY_PID','GNAF_STREET_PID'])
    locality = pd.read_csv(f"{data_path}/VIC_LOCALITY_psv.psv", sep="|", dtype='str', usecols=['LOCALITY_PID','LOCALITY_NAME','GNAF_LOCALITY_PID','STATE_PID'])
    state    = pd.read_csv(f"{data_path}/VIC_STATE_psv.psv", sep="|", dtype='str', usecols=['STATE_PID','STATE_NAME','STATE_ABBREVIATION'])
    geocodedf  = pd.read_csv(f"{data_path}/VIC_ADDRESS_DEFAULT_GEOCODE_psv.psv", sep="|", dtype='str', usecols=['ADDRESS_DETAIL_PID','LONGITUDE','LATITUDE'])


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
    dfaddress["full_address"].str.replace(r'\s+', ' ', regex=True).str.strip()
    org_shape = dfaddress.shape
    dfaddress = dfaddress[['ADDRESS_DETAIL_PID', 'full_address']].merge(
        geocodedf, on='ADDRESS_DETAIL_PID', how='left')

    if dfaddress.shape[0] != org_shape[0]:
        print("Warning: Row count mismatch after merging geocoded data!")
        
    
    print("Building BM25 index...")
    dfaddress["full_address_clean"] = dfaddress["full_address"].apply(sanitize_input_string)
    #dfaddress["full_address_clean"].str.replace(r'\s+', ' ', regex=True).str.strip()
    bm25 = BM25Okapi(dfaddress["full_address_clean"].str.split().tolist())

    print("Building address lookup...")
    address_lookup = dfaddress.drop_duplicates(subset=['full_address_clean']).set_index("full_address_clean").to_dict(orient="index")

    return dfaddress, bm25, address_lookup


def save_gnaf(df, output_path: str):
    df.to_parquet(output_path, index=False)
    print(f"Saved to {output_path}")
    
    
def load_gnaf_from_s3(bucket: str, key: str, sample: int = None):
    print("Loading GNAF from S3...")
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    df = pd.read_parquet(io.BytesIO(obj["Body"].read()))

    if sample:
        df = df.sample(sample, random_state=42).reset_index(drop=True)
        print(f"Sampled {sample} rows")
    print("Building BM25 index...")
    corpus = [addr.split() for addr in df["full_address_clean"]]
    bm25 = BM25Okapi(corpus)

    print("Building address lookup...")
    address_lookup = df.set_index("full_address_clean").to_dict(orient="index")

    print("Ready")
    return df, bm25, address_lookup