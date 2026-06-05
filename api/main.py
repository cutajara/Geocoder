from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager
from api.data_loader import load_gnaf, load_gnaf_from_s3
from api.geocoder import geocode
from concurrent.futures import ThreadPoolExecutor


RUNMODE = "local"  # Change to "aws" for AWS Lambda

LOCAL_PATH = "DataPrep/gnaf_addresses.parquet"
AWS_BUCKET = "geocoder-gnaf-vic"
AWS_OBJECT_KEY = "gnaf_vic_sample.parquet"

# --- Lifespan --- loads index once on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Loading GNAF index...")
    
    if RUNMODE == "local":
        app.state.df, app.state.bm25, app.state.address_lookup = load_gnaf(
            data_path=LOCAL_PATH
        )
    else:
        app.state.df, app.state.bm25, app.state.address_lookup = load_gnaf_from_s3(
            bucket="geocoder-gnaf-vic",
            key="gnaf_vic_sample.parquet",
            #sample=100000 # REmove for production
        )
    print("Ready")
    yield

app = FastAPI(title="GNAF Geocoder", lifespan=lifespan)

# --- Request/Response models ---
class GeocodeRequest(BaseModel):
    address: str
    n_candidates: int = 10

class GeocodeResponse(BaseModel):
    gnaf_pid: str
    matched_address: str
    input_address: str
    latitude: float
    longitude: float
    confidence: float
    
class BatchGeocodeRequest(BaseModel):
    addresses: list[str]
    n_candidates: int = 10
    
class AddressLookupResponse(BaseModel):
    gnaf_pid: str
    input_address: str
    latitude: float
    longitude: float

# --- Endpoints ---
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/address", response_model=AddressLookupResponse)
def get_address(address: str):
    row = app.state.address_lookup.get(address.upper())
    if not row:
        raise HTTPException(status_code=404, detail="Address not found")
    return {
        "gnaf_pid":      row["ADDRESS_DETAIL_PID"],
        "input_address": address,
        "latitude":      row["LATITUDE"],
        "longitude":     row["LONGITUDE"],
    }


@app.post("/geocode", response_model=GeocodeResponse)
def geocode_address(request: GeocodeRequest):
    try:
        result = geocode(
            request.address,
            app.state.df,
            app.state.bm25,
            n=request.n_candidates
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    

@app.post("/geocode/batch")
def geocode_batch(request: BatchGeocodeRequest):
    with ThreadPoolExecutor() as executor:
        results = list(executor.map(
            lambda addr: geocode(addr, app.state.df, app.state.bm25, n=request.n_candidates),
            request.addresses
        ))
    return results

