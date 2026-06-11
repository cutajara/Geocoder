from aws_cdk import (
    Stack,
    aws_s3 as s3,
    aws_ecs as ecs,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_elasticloadbalancingv2 as elbv2,
    CfnOutput,
    RemovalPolicy,
)
from constructs import Construct

class GeocoderStack(Stack):

    def __init__(self, scope: Construct, construct_id: str,
                 gnaf_url: str,
                 gnaf_month_release: str,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- S3 Bucket ---
        bucket = s3.Bucket(
            self, "GnafBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # --- VPC ---
        # NAT Gateway allows ECS in private subnets to reach ECR and S3
        vpc = ec2.Vpc(
            self, "GeocoderVpc",
            max_azs=2,
            nat_gateways=1
        )

        # --- ECS Cluster ---
        cluster = ecs.Cluster(self, "GeocoderCluster", vpc=vpc)

        # --- Task Execution Role ---
        execution_role = iam.Role(
            self, "TaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ]
        )

        # --- Task Role (app code permissions) ---
        task_role = iam.Role(
            self, "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        bucket.grant_read_write(task_role)

        # --- Processing Task Definition ---
        processing_task = ecs.FargateTaskDefinition(
            self, "ProcessingTask",
            cpu=2048,
            memory_limit_mib=8192,
            execution_role=execution_role,
            task_role=task_role
        )
        processing_task.add_container(
            "ProcessingContainer",
            image=ecs.ContainerImage.from_registry(
                f"{self.account}.dkr.ecr.ap-southeast-2.amazonaws.com/geocoder:latest"
            ),
            environment={
                "RUN_MODE": "process",
                "GNAF_URL": gnaf_url,
                "GNAF_MONTH_RELEASE": gnaf_month_release,
                "GNAF_BUCKET": bucket.bucket_name,
                "GNAF_KEY": "gnaf_addresses.parquet"
                "AWS_DEFAULT_REGION": "ap-southeast-2"
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="geocoder-processing")
        )

        # --- API Task Definition ---
        api_task = ecs.FargateTaskDefinition(
            self, "ApiTask",
            cpu=1024,
            memory_limit_mib=4096,
            execution_role=execution_role,
            task_role=task_role
        )
        api_task.add_container(
            "ApiContainer",
            image=ecs.ContainerImage.from_registry(
                f"{self.account}.dkr.ecr.ap-southeast-2.amazonaws.com/geocoder:latest"
            ),
            environment={
                "RUN_MODE": "serve",
                "GNAF_BUCKET": bucket.bucket_name,
                "GNAF_KEY": "gnaf_vic_sample.parquet",
                "AWS_DEFAULT_REGION": "ap-southeast-2"
            },
            port_mappings=[ecs.PortMapping(container_port=8000)],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="geocoder-api")
        )

        # --- Security Groups ---
        vpc_link_sg = ec2.SecurityGroup(
            self, "VpcLinkSecurityGroup",
            vpc=vpc,
            description="Security group for API Gateway VPC Link"
        )

        alb_sg = ec2.SecurityGroup(
            self, "AlbSecurityGroup",
            vpc=vpc,
            description="Security group for internal ALB"
        )
        alb_sg.add_ingress_rule(
            vpc_link_sg,
            ec2.Port.tcp(80),
            "Allow API Gateway VPC Link to reach ALB listener"
        )

        api_sg = ec2.SecurityGroup(
            self, "ApiSecurityGroup",
            vpc=vpc,
            description="Security group for Geocoder API tasks"
        )
        api_sg.add_ingress_rule(
            alb_sg,
            ec2.Port.tcp(8000),
            "Allow ALB to reach API container"
        )

        # --- API ECS Service ---
        # Private subnets — NAT Gateway handles outbound internet access
        api_service = ecs.FargateService(
            self, "ApiService",
            cluster=cluster,
            task_definition=api_task,
            desired_count=1,
            security_groups=[api_sg],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            )
        )

        # --- Internal Load Balancer ---
        # Internal ALB — only accessible via VPC Link from API Gateway
        alb = elbv2.ApplicationLoadBalancer(
            self, "GeocoderALB",
            vpc=vpc,
            internet_facing=False,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            )
        )
        listener = alb.add_listener("Listener", port=80)
        listener.add_targets(
            "ApiTarget",
            port=8000,
            targets=[api_service],
            health_check=elbv2.HealthCheck(path="/health")
        )

        # --- VPC Link ---
        # Private connection from API Gateway to internal ALB
        vpc_link = apigw.VpcLink(
            self, "VpcLink",
            vpc=vpc,
            security_groups=[vpc_link_sg],
            subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            )
        )

        # --- API Gateway ---
        http_api = apigw.HttpApi(self, "GeocoderApi")

        http_api.add_routes(
            path="/geocode",
            methods=[apigw.HttpMethod.POST],
            integration=integrations.HttpAlbIntegration(
                "GeocodeIntegration",
                listener=listener,
                vpc_link=vpc_link
            )
        )
        http_api.add_routes(
            path="/geocode/batch",
            methods=[apigw.HttpMethod.POST],
            integration=integrations.HttpAlbIntegration(
                "BatchGeocodeIntegration",
                listener=listener,
                vpc_link=vpc_link
            )
        )
        http_api.add_routes(
            path="/address",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpAlbIntegration(
                "AddressIntegration",
                listener=listener,
                vpc_link=vpc_link
            )
        )
        http_api.add_routes(
            path="/health",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpAlbIntegration(
                "HealthIntegration",
                listener=listener,
                vpc_link=vpc_link
            )
        )

        # --- Outputs ---
        CfnOutput(self, "ApiUrl", value=http_api.url)
        CfnOutput(self, "BucketName", value=bucket.bucket_name)