import sys
import os

def main():
    mode = os.environ.get("RUN_MODE", "serve")
    
    if mode == "process":
        print("Starting GNAF processing...")
        from processor import downloader
        downloader.run_pipeline()
        print("Processing complete")

    else:
        print("Running in server/lambda gateway mode.")



if __name__ == "__main__":
    main()