import sys
import os

def main():
    mode = os.environ.get("RUN_MODE", "serve")
    
    if mode == "process":
        print("Starting GNAF processing...")
        from processor.downloader import download_and_process_gnaf
        download_and_process_gnaf(
            url=os.environ["GNAF_URL"],
            month_release=os.environ["GNAF_MONTH_RELEASE"],
            bucket=os.environ["GNAF_BUCKET"],
            key="gnaf_addresses.parquet"
        )
        print("Processing complete")

    elif mode == "serve":
        print("Starting geocoder API...")
        import uvicorn
        uvicorn.run("api.main:app", host="0.0.0.0", port=8000)

    else:
        print(f"Unknown RUN_MODE: {mode}")
        sys.exit(1)

if __name__ == "__main__":
    main()