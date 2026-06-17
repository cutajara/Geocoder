import json
from geocoder import geocode_address

def handler(event, context):
    print("Received API Event:", json.dumps(event))
    
    # Grab the user query string parameters out of the HTTP call
    query_params = event.get("queryStringParameters") or {}
    address_query = query_params.get("address")
    
    if not address_query:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Missing required 'address' query string parameter."})
        }
    
    try:
        # Search OpenSearch and get matched results
        matches = geocode_address(address_query)
        
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*" # Enables clean web app CORS access!
            },
            "body": json.dumps({
                "query": address_query,
                "results": matches
            })
        }
        
    except Exception as e:
        print("API Layer Error:", str(e))
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "An internal error occurred matching the location request."})
        }