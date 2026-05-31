
import re
import json
from rapidfuzz import process, fuzz

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

def search_addresses(query_string, bm25, n=10):
    tokens = query_string.upper().split()
    scores = bm25.get_scores(tokens)
    top_n_idx = scores.argsort()[::-1][:n]
    
    return top_n_idx
  
def create_output(match, score, input_string, dfaddress):
    match_idx = dfaddress[dfaddress['full_address'] == match].index[0]
    matched_address = dfaddress.loc[match_idx]
    matched_address = json.loads(matched_address.to_json())
    matched_address['score'] = score
    matched_address['InputAddress'] = input_string
    return matched_address
  
  
def geocode(input_string, df, bm25, n=10):
    try:
        santized_input_string = sanitize_input_string(input_string)
        search_results = search_addresses(santized_input_string, bm25, n=n)   
        match, score, _ = process.extractOne(santized_input_string, df.loc[search_results,'full_address'], scorer=fuzz.token_sort_ratio)


        match_idx = df[df["full_address"] == match].index[0]
        row = df.loc[match_idx]

        return {
            "gnaf_pid":       row["ADDRESS_DETAIL_PID"],
            "matched_address": match,
            "input_address":  input_string,
            "latitude":       row["LATITUDE"],
            "longitude":      row["LONGITUDE"],
            "confidence":     round(score / 100, 4)
        }
    except Exception as e:
        print(f"Error geocoding address: {e}")
        return {
            "gnaf_pid": None,
            "matched_address": None,
            "input_address": input_string,
            "latitude": None,
            "longitude": None,
            "confidence": 0.0
        }
        
        

