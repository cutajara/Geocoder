# Geocoder
Geocode Australia address from raw text


## Docker App


## To run the dev container
docker run -it \
  -v ${PWD}:/app \
  -e AWS_ACCESS_KEY_ID=your-key \
  -e AWS_SECRET_ACCESS_KEY=your-secret \
  -e AWS_DEFAULT_REGION=ap-southeast-2 \
  -e AWS_ACCOUNT_ID=you-account-id
  geocoder-dev /bin/bash
  
  
 When inside
 - cd infra
 - cdk bootstrap aws://your-account-id/ap-southeast-2
 - cdk deploy
 
 
 ## Prepare the GNAF