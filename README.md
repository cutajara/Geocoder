# Geocoder
Geocode Australia address from raw text

Users can deploy this to there AWS Account with their Account ID and URL to latest GNAF dataset.

```mermaid

graph TD
    %% Define Styles
    classDef github fill:#24292e,stroke:#fff,stroke-width:1px,color:#fff;
    classDef awsPublic fill:#FF9900,stroke:#232F3E,stroke-width:1px,color:#232F3E;
    classDef awsPrivate fill:#3F8624,stroke:#232F3E,stroke-width:1px,color:#fff;
    classDef external fill:#5A6B7C,stroke:#232F3E,stroke-width:1px,color:#fff;

    %% External & CI/CD
    subgraph GitHub_Platform ["GitHub"]
        GA[GitHub Actions Workflow]:::github
        ECR[(Amazon ECR<br>Private Registry)]:::github
    end

    User([Local PC / Developer CLI]):::external
    DataGov[(Data.gov.au<br>G-NAF Zip Source)]:::external

    %% AWS Cloud Infrastructure
    subgraph AWS_Cloud ["AWS Cloud (selected region)"]
        
        %% Public Layer
        subgraph Public_Subnet ["VPC Public Subnet (Max AZs: 1)"]
            APIGW[Amazon API Gateway<br>⚡ Throttling: 10 rps / 20 burst]:::awsPublic
            NAT[NAT Gateway]:::awsPublic
        end

        %% Private Layer
        subgraph Private_Subnet ["VPC Private Subnet (With Egress)"]
            Fargate[ECS Fargate Ingestion Task<br>📦 Chunked Pandas Stream Engine]:::awsPrivate
            Lambda[AWS Lambda Function<br>🔍 API Query Handler]:::awsPrivate
            OpenSearch[(Amazon OpenSearch Service<br>t3.small.search / 0 Replicas)]:::awsPrivate
        end

    end

    %% CI/CD Flow
    GA -->|1. Pushes Docker Image| ECR
    GA -->|2. cdk deploy| AWS_Cloud
    GA -->|3. aws ecs run-task| Fargate

    %% Ingestion Pipeline Flow
    Fargate -->|A. Pulls Image| ECR
    Fargate -->|B. Downloads Zip via Egress| NAT
    NAT --> DataGov
    Fargate -->|C. Formatted Bulk Upload| OpenSearch

    %% Live Runtime / API Traffic Flow
    User -->|1. HTTP GET /geocode_address| APIGW
    APIGW -->|2. Validates Limits & Forwards Proxy| Lambda
    Lambda -->|3. Fuzzy SigV4 Match Query| OpenSearch
    OpenSearch -->|4. Coordinates Data Response| Lambda
    Lambda -->|5. Returns 200 OK JSON JSON| APIGW
    APIGW -->|6. Geocoded Result| User
```
    
## Pipeline
- To download the large GNAF dataset, run a Fargate container. Stream to download to manage RAM
- Process one state at a time and upload to OpenSearch
- OpenSearch handels the address matching and similarity

When deployed, users will recieve a API Gateway URL.
When requests are received they are sent to a lambda function which queries OpenSearch and returns the matches.

The endpoint to send requests is:
curl -X POST 

## Deploying the App
Your PC will need:
- Python
- awscli-2
- Either Docker OR nodejs (nodejs is needed for aws cdk. If not avalible, you can use the Docker.dev container)

### To install awscli-2 (In PowerShell)
Invoke-WebRequest -Uri "https://awscli.amazonaws.com/AWSCLIV2.msi" -OutFile "$env:USERPROFILE\Downloads\AWSCLIV2.msi"
msiexec /a "$env:USERPROFILE\Downloads\AWSCLIV2.msi" /qb TARGETDIR="$env:USERPROFILE\awscli"
Set-Alias -Name aws -Value "$env:USERPROFILE\awscli\Amazon\AWSCLIV2\aws.exe"
aws configure

# Create the repo
aws ecr create-repository --repository-name gnaf-processor --region ap-southeast-2

# Log your local Docker into your private AWS Registry
aws ecr get-login-password --region ap-southeast-2 | docker login --username AWS --password-stdin <YOUR_ACCOUNT_ID>.dkr.ecr.ap-southeast-2.amazonaws.com

# Build, tag, and push!
docker build -t gnaf-processor:latest .
docker tag gnaf-processor:latest <YOUR_ACCOUNT_ID>.dkr.ecr.ap-southeast-2.amazonaws.com/gnaf-processor:latest
docker push <YOUR_ACCOUNT_ID>.dkr.ecr.ap-southeast-2.amazonaws.com/gnaf-processor:latest

## Build build dev container
docker build -t geocoder-dev -f Dockerfile.dev .

## To run the dev container (if awscdk not installed on your PC)
docker run -it -v ${PWD}:/app -e AWS_ACCESS_KEY_ID=your-key -e AWS_SECRET_ACCESS_KEY=your-secret-e AWS_DEFAULT_REGION=ap-southeast-2 -e AWS_ACCOUNT_ID=you-account-id geocoder-dev /bin/bash
  
  
 When inside
 - cdk bootstrap --app echo []
 - cdk deploy --parameters GnafUrl="https://data.gov.au/data/dataset/19432f89-dc3a-4ef3-b943-5326ef1dbecc/resource/f8666213-4079-44da-bede-ebda3a4363e0/download/g-naf_may26_allstates_gda2020_psv_1023.zip" --parameters GnafMonthRelease="MAY 2026" --parameters AwsAccountId="<YOUR_ACCOUNT_ID>"
 


 # Once deployed:
 To curl the API:
 curl -X GET "https://xxxxxxxxxx.execute-api.ap-southeast-2.amazonaws.com/prod/geocode_address?address=100+GeorgeSt+Sydney"
