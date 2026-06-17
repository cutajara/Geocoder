
import re
import os
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

# These environment variables will be injected automatically by AWS CDK
OPENSEARCH_ENDPOINT = os.environ['OPENSEARCH_ENDPOINT']
AWS_REGION = os.environ.get('AWS_REGION', 'ap-southeast-2')

def get_opensearch_client():
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

def geocode_address(search_string):
    client = get_opensearch_client()
    
    search_string_sanitised = sanitize_input_string(search_string)
    
    query = {
        "query": {
            "match": {
                "full_address": {
                    "query": search_string_sanitised,
                    "fuzziness": "AUTO",
                    "operator": "or"
                }
            }
        },
        "size": 3
    }
    
    response = client.search(index="gnaf", body=query)
    hits = response['hits']['hits']
    
    results = []
    for hit in hits:
        results.append({
            "address": hit['_source']['full_address'],
            "latitude": hit['_source']['latitude'],
            "longitude": hit['_source']['longitude'],
            "score": hit['_score']
        })
    return results



def sanitize_input_string(input_string: str) -> str:

  STREET_ABBR = {
    "ST" :"STREET",
    "AV" :"AVENUE",
    "AVE" :"AVENUE",
    "RD" :"ROAD",
    "DR" :"DRIVE",
    "CRT" :"COURT",
    "PL" :"PLACE",
    "LN" :"LANE",
    "CIR" :"CIRCLE",
    "TCE" :"TERRACE",
    "BLVD" :"BOULEVARD",
    "PKWY" :"PARKWAY",
    "HWY" :"HIGHWAY",
    "SQ" :"SQUARE",
    "GR" :"GROVE",
    "PDE" :"PARADE",
    "CRES" :"CRESCENT"
  }

  addrinput_string = re.sub(r'[,.]', '', input_string)  # Remove punctuation except "/"
  upper_list = addrinput_string.upper().strip().strip().split()
  expanded_list = [STREET_ABBR.get(word, word) for word in upper_list]
  expanded_list

  santized_input_string = " ".join(expanded_list)
  #print(f"Sanitized input string: {santized_input_string}")
  return santized_input_string



