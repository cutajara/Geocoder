
import pandas as pd
from rank_bm25 import BM25Okapi
import pyarrow.parquet as pq
import boto3
from api.geocoder import sanitize_input_string
import io
import os
    
def build_geocoder_inputs(df):
    print("Building BM25 index...")
    df["full_address_clean"] = df["full_address"].apply(sanitize_input_string)
    corpus = [addr.split() for addr in df["full_address_clean"]]
    print(df.shape[0], "starting addresses")
    df.drop_duplicates(subset=["full_address_clean"], inplace=True)
    print(df.shape[0], "unique addresses after deduplication")
    bm25 = BM25Okapi(corpus)

    print("Building address lookup...")
    address_lookup = df.set_index("full_address_clean").to_dict(orient="index")

    print("Ready")
    return df, bm25, address_lookup
    
def load_gnaf(data_path: str):
    print(f"Loading GNAF from {data_path}...")
    df = pd.read_parquet(data_path)
    df, bm25, address_lookup = build_geocoder_inputs(df)
    return df, bm25, address_lookup
    
def load_gnaf_from_s3(sample: int = None):
    bucket = os.environ.get("GNAF_BUCKET")
    key = os.environ.get("GNAF_KEY", "gnaf_vic_sample.parquet")
    print("Loading GNAF from S3...")
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    buffer = io.BytesIO(obj["Body"].read())
    parquet_file = pq.ParquetFile(buffer)
    
    if sample:
        # Read only first N rows
        df = next(parquet_file.iter_batches(batch_size=sample)).to_pandas()
    else:
        df = pd.read_parquet(buffer)

    df, bm25, address_lookup = build_geocoder_inputs(df)

    return df, bm25, address_lookup