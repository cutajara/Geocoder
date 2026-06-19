# Geocoder
Geocode Australian address from raw text

Deploy this to your AWS Account with Account ID and URL to latest GNAF dataset. Service is written in IaC (aws-cdk) for automatic deployment. Can then be destroyed when not needed.

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
        
    end

    User([Local PC / Developer CLI]):::external
    DataGov[(Data.gov.au<br>G-NAF Zip Source)]:::external

    %% AWS Cloud Infrastructure
    subgraph AWS_Cloud ["AWS Cloud (selected region)"]
        ECR[(Amazon ECR<br>Private Registry)]
        %% Public Layer
        subgraph Public_Subnet ["VPC Public Subnet (Max AZs: 1)"]
            APIGW[Amazon API Gateway<br>Throttling: 10 rps / 20 burst]:::awsPublic
            NAT[NAT Gateway]:::awsPublic
        end

        %% Private Layer
        subgraph Private_Subnet ["VPC Private Subnet (With Egress)"]
            Fargate[ECS Fargate Ingestion Task<br>Chunked Pandas Stream Engine]:::awsPrivate
            Lambda[AWS Lambda Function<br>API Query Handler]:::awsPrivate
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
1. Downloads GNAF dataset from provided URL. This runs in a Fargate container, streaming the download to manage RAM
2. Process one state at a time and upload to OpenSearch
3. OpenSearch handels the address matching and similarity
4. API Gateway is created which routes to a lambda function to query OpenSearch

When deployed, users will recieve an API Gateway URL.
When requests are received they are sent to a lambda function which queries OpenSearch and returns the matches.

 ### Once deployed:
 To curl the API:
 ```curl -X GET "https://xxxxxxxxxx.execute-api.ap-southeast-2.amazonaws.com/prod/geocode_address?address=100+GeorgeSt+Sydney"```

## Deploying the App
Deploy with GitHub Actions, there is a deploy.yml, that creates the infrastucture in your AWS account. This runs the pipelines and creates the infrastructure, returning a URL link to send requests to.
Once you are done, run destroy.yml to teardown the infrustruction and not incur costs.

Three environment variables are required (assign in Secrets and varaibles):
1. AWS Account Number
2. AWS User Access Key
3. AWS User Access Secret

When deploying, there is a region parameter to deploy to your prefered region.

The API returns the 5 closest addresses to the users input, with the lat and long of the address.

Enjoy :)
