import tempfile
import pulumi
import pulumi_aws as aws
import pulumi_eks as eks
import pulumi_docker as docker
import pulumi_kubernetes as k8s

# CREAR EKS
# zonas válidas
zonas = [z for z in aws.get_availability_zones(state="available").names if z != "us-east-1e"]
# vpc
vpc = aws.ec2.Vpc(
    "eks-vpc",
    cidr_block="10.100.0.0/16",
    enable_dns_hostnames=True,
    enable_dns_support=True
)
igw = aws.ec2.InternetGateway("eks-igw", vpc_id=vpc.id)
# subnets públicas
subnets = []
for i, zone in enumerate(zonas[:2]):
    subnet = aws.ec2.Subnet(
        f"eks-subnet-{zone}",
        vpc_id=vpc.id,
        cidr_block=f"10.100.{i}.0/24",
        availability_zone=zone,
        map_public_ip_on_launch=True
    )
    subnets.append(subnet)
# ruta pública
route_table = aws.ec2.RouteTable("eks-route-table",
        vpc_id=vpc.id,
        routes=[aws.ec2.RouteTableRouteArgs(
                cidr_block="0.0.0.0/0",
                gateway_id=igw.id
            )
        ]
    )
for subnet in subnets:
    aws.ec2.RouteTableAssociation(f"eks-rta-{subnet._name}", route_table_id=route_table.id, subnet_id=subnet.id)
# EKS dentro de esa VPC
cluster = eks.Cluster(
    "app-cluster",
    vpc_id=vpc.id,
    subnet_ids=[s.id for s in subnets],
    version="1.29",
    create_oidc_provider=True,
    enabled_cluster_log_types=["api", "audit", "authenticator"],
    node_group_options = eks.ClusterNodeGroupOptionsArgs(
        instance_type="t3.small",
        desired_capacity=2,
        min_size=2,
        max_size=5,
    )
)
proveedor = k8s.Provider("k8s-provider", kubeconfig=cluster.kubeconfig)
pulumi.export("cluster_name", cluster.core.cluster.name)
pulumi.export("kubeconfig", cluster.kubeconfig)

# ECR + IMÁGENES
auth_token = aws.ecr.get_authorization_token()
backend_repo = aws.ecr.Repository("backend-repo")
frontend_repo = aws.ecr.Repository("frontend-repo")
def crear_imagen(name, path, repo):
    return docker.Image(
        name,
        build=docker.DockerBuildArgs(context=path, platform="linux/amd64"),
        image_name=pulumi.Output.concat(repo.repository_url, ":latest"),
        registry=pulumi.Output.all(repo.repository_url, auth_token).apply(
            lambda args: {"server": args[0], "username": args[1].user_name, "password": args[1].password}
        ),
    )
backend_img = crear_imagen("backend-image", "../backend", backend_repo)
frontend_img = crear_imagen("frontend-image", "../frontend", frontend_repo)

# RDS MYSQL CON AUTOSCALING
db_subnet_group = aws.rds.SubnetGroup(
    "db-subnet-group",
    subnet_ids=[s.id for s in subnets],
    tags={"Name": "mysql-subnet-group"},
)
db_sg = aws.ec2.SecurityGroup(
    "db-sg",
    vpc_id=vpc.id,
    description="Permitir acceso MySQL desde el cluster EKS",
    ingress=[aws.ec2.SecurityGroupIngressArgs(
        protocol="tcp", from_port=3306, to_port=3306, cidr_blocks=["10.100.0.0/16"]
    )],
    egress=[aws.ec2.SecurityGroupEgressArgs(
        protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"]
    )],
)
rds_instance = aws.rds.Instance(
    "mysql-db",
    allocated_storage=20,
    max_allocated_storage=100,
    engine="mysql",
    engine_version="8.0",
    instance_class="db.t3.micro",
    db_name="appdb",
    username="admin",
    password="Admin12345!",
    db_subnet_group_name=db_subnet_group.name,
    vpc_security_group_ids=[db_sg.id],
    skip_final_snapshot=True,
    publicly_accessible=False,
    storage_encrypted=True,
    backup_retention_period=7,
)

# SECRET - RDS
rds_secret = k8s.core.v1.Secret(
    "db-connection",
    metadata={"name": "db-connection"},
    string_data={
        "DB_HOST": rds_instance.address,
        "DB_USER": "admin",
        "DB_PASSWORD": "Admin12345!",
        "DB_NAME": "appdb",
    },
    opts=pulumi.ResourceOptions(provider=proveedor),
)

# YAMLS Y ECR
def yaml_temporal(base_path, replacements):
    with open(base_path, "r") as f:
        content = f.read()
    for local, ecr_url in replacements.items():
        content = content.replace(local, ecr_url)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".yaml")
    tmp.write(content.encode())
    tmp.close()
    return tmp.name

backend_ruta = pulumi.Output.all(backend_img.image_name).apply(
    lambda args: yaml_temporal("../backend.yaml", {"local/backend:latest": args[0]})
)
frontend_ruta = pulumi.Output.all(frontend_img.image_name).apply(
    lambda args: yaml_temporal("../frontend.yaml", {"local/frontend:latest": args[0]})
)

backend = backend_ruta.apply(
    lambda path: k8s.yaml.ConfigFile(
        "backend",
        file=path,
        opts=pulumi.ResourceOptions(provider=proveedor, depends_on=[rds_secret]),
    )
)
frontend = frontend_ruta.apply(
    lambda path: k8s.yaml.ConfigFile(
        "frontend",
        file=path,
        opts=pulumi.ResourceOptions(provider=proveedor, depends_on=[backend]),
    )
)

# AUTOSCALER BACKEND
backend_scaler = k8s.autoscaling.v2.HorizontalPodAutoscaler(
    "backend-hpa",
    metadata={"name": "backend-hpa"},
    spec={
        "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "backend"},
        "minReplicas": 1,
        "maxReplicas": 5,
        "metrics": [{
            "type": "Resource",
            "resource": {"name": "cpu", "target": {"type": "Utilization", "averageUtilization": 50}},
        }],
    },
    opts=pulumi.ResourceOptions(provider=proveedor, depends_on=[backend]),
)

# EXPORTS
pulumi.export("cluster_name", cluster.core.cluster.name)
pulumi.export("rds_endpoint", rds_instance.address)
pulumi.export("rds_username", rds_instance.username)
pulumi.export("rds_dbname", rds_instance.db_name)
pulumi.export("backend_repo", backend_repo.repository_url)
pulumi.export("frontend_repo", frontend_repo.repository_url)