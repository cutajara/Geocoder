from aws_cdk import (
    Stack,
    CfnParameter,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_opensearchservice as opensearch,
    aws_iam as iam,
    aws_ecr_assets as ecr_assets  # 1. Added for Docker-less cloud asset bundling
)
from constructs import Construct
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_logs as logs
from aws_cdk import aws_lambda as _lambda
import aws_lambda_python_alpha as lambda_python
from aws_cdk import aws_apigateway as apigw
from aws_cdk import CfnOutput

class GeocoderStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Configurable Runtime Parameters (No hardcoded data URLs)
        gnaf_url = CfnParameter(self, "GnafUrl", type="String", 
                                description="The direct secure download URL for the GNAF zip archive")
        gnaf_month = CfnParameter(self, "GnafMonthRelease", type="String",
                                  description="The release tag month (e.g., May2026)")
        awsAccount = CfnParameter(self, "AwsAccountId", type="String",
                                  description="Your AWS Account ID (for ECR image reference)")
        
        ecr_region = self.region  # Dynamically reference the deployment region for ECR image sourcing

        # 2. Build an Isolated Network Environment
        vpc = ec2.Vpc(
            self, "GnafVpc",
            max_azs=1,  # Kept to 1 for cost efficiency on our t3.small sandbox cluster
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public", 
                    subnet_type=ec2.SubnetType.PUBLIC, 
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="PrivateWithEgress", 
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, 
                    cidr_mask=24
                )
            ]
        )

        # 3. Security Groups (Firewalls)
        opensearch_sg = ec2.SecurityGroup(
            self, "OpenSearchSG",
            vpc=vpc,
            description="Control inbound access directly to the OpenSearch cluster",
            allow_all_outbound=True
        )

        # Allow any resource inside the VPC network to talk to OpenSearch on HTTPS Port 443
        opensearch_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(443),
            description="Allow private inside-VPC traffic to query addresses"
        )

        # 4. Provision Serverless-ready Amazon OpenSearch Domain
        opensearch_domain = opensearch.Domain(
            self, "GnafOpenSearchDomain",
            version=opensearch.EngineVersion.OPENSEARCH_3_5,
            capacity=opensearch.CapacityConfig(
                data_node_instance_type="t3.small.search",
                data_nodes=1
            ),
            ebs=opensearch.EbsOptions(
                volume_size=15,  # 15 GB allocation tailored for micro pricing
                volume_type=ec2.EbsDeviceVolumeType.GP3
            ),
            vpc=vpc,
            security_groups=[opensearch_sg],
#            vpc_subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)],
            vpc_subnets=vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS).subnets,
            
            # Open VPC Access Policy: Controlled entirely via Security Groups for safety
            access_policies=[
                iam.PolicyStatement(
                    actions=["es:*"],
                    effect=iam.Effect.ALLOW,
                    principals=[iam.AnyPrincipal()],
                    resources=[f"arn:aws:es:{self.region}:{self.account}:domain/*"]
                )
            ],
            removal_policy=RemovalPolicy.DESTROY  # Erases the domain entirely on 'cdk destroy'
        )
        # 5. Create an ECS Cluster to host our one-off container execution
        cluster = ecs.Cluster(self, "GnafEcsCluster", vpc=vpc)

        # 6. Define the Serverless Fargate Task
        fargate_task = ecs.FargateTaskDefinition(
            self, "GnafIngestionTask",
            memory_limit_mib=4096,
            cpu=2048
        )

        # 7. Link Your Pre-built Code Registry
        # By pointing directly to your remote ECR URL, your dev container
        # never has to execute local docker commands during deployment.
        container = fargate_task.add_container(
            "GnafProcessorContainer",
            image=ecs.ContainerImage.from_registry(f"{awsAccount.value_as_string}.dkr.ecr.{ecr_region}.amazonaws.com/gnaf-processor:latest"), # COME BACK AND ADJUST FOR DIFFERNT REGION@
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="gnaf-processor",
                log_retention=logs.RetentionDays.ONE_WEEK
            ),
            environment={
                "OPENSEARCH_ENDPOINT": opensearch_domain.domain_endpoint,
                "GNAF_URL": gnaf_url.value_as_string,
                "GNAF_RELEASE": gnaf_month.value_as_string,
                "MODE": "process"
            }
        )

        # 8. Grant SigV4 access permissions so the container can write to OpenSearch
        opensearch_domain.grant_write(fargate_task.task_role)
    
        # 9. Create the Serverless Lambda function with Auto-Dependency Bundling
        geocoder_lambda = lambda_python.PythonFunction(
            self, "GnafQueryLambda",
            entry="./api",                      # Points to your directory containing lambda_handler.py
            index="lambda_handler.py",          # The file name
            handler="handler",                  # The function inside that file
            runtime=_lambda.Runtime.PYTHON_3_11,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[opensearch_sg],
            environment={
                "OPENSEARCH_ENDPOINT": opensearch_domain.domain_endpoint,
                "MODE": "serve"
            }
        )

        # 10. Grant the Lambda permission to query (Read) OpenSearch
        opensearch_domain.grant_read(geocoder_lambda.role)

        # 11. Wrap it with an API Gateway so you can hit it over the web
        api = apigw.LambdaRestApi(
            self, "GnafGeocoderApi",
            handler=geocoder_lambda,
            proxy=True, # Forwards all paths directly to our Lambda handler
            description="Public endpoint for our Australian address geocoder"
        )

        # 12. Output the VPC ID for the GitHub Actions pipeline to read
        CfnOutput(self, "GnafVpcId", value=vpc.vpc_id)
