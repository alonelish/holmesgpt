# Kind (Kubernetes in Docker) Setup for HolmesGPT

This document describes the setup process for running Kind clusters for HolmesGPT development and testing.

## Quick Start

Run the automated setup script:

```bash
./kind-setup.sh
```

## Manual Installation

### Prerequisites

- Linux system with kernel support for Docker networking
- Docker installed and running
- At least 4GB RAM available

### Install Tools

```bash
# Add ~/bin to PATH
export PATH="$HOME/bin:$PATH"

# Install kubectl
KUBECTL_VERSION=$(curl -L -s https://dl.k8s.io/release/stable.txt)
curl -Lo ~/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
chmod +x ~/bin/kubectl

# Install Kind
KIND_VERSION="v0.26.0"
curl -Lo ~/bin/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
chmod +x ~/bin/kind
```

### Create a Kind Cluster

```bash
# Create a simple single-node cluster
kind create cluster --name holmesgpt-test

# Verify the cluster is running
kubectl cluster-info --context kind-holmesgpt-test
kubectl get nodes
```

### Custom Cluster Configuration

For more advanced setups, create a configuration file:

```yaml
# kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        node-labels: "ingress-ready=true"
  extraPortMappings:
  - containerPort: 80
    hostPort: 8080
    protocol: TCP
  - containerPort: 443
    hostPort: 8443
    protocol: TCP
```

Then create the cluster:

```bash
kind create cluster --name holmesgpt-test --config kind-config.yaml
```

## Environment Limitations

### Restricted/Containerized Environments

This environment (where the initial setup was performed) has kernel restrictions that prevent Docker networking from functioning properly. This is a common limitation when running Docker inside containers without privileged access.

**Error encountered:**
```
Cannot read IPv4 local routing setup: open /proc/sys/net/ipv4/conf/br-*/route_localnet: no such file or directory
```

**What works:**
- ✓ kubectl installation and usage with external clusters
- ✓ Kind installation
- ✓ Docker daemon (with `--bridge=none` flag)
- ✓ Docker container operations (without networking)

**What doesn't work:**
- ✗ Docker network creation
- ✗ Kind cluster creation (requires Docker networks)
- ✗ Container-to-container networking

### Solutions for Development

If you encounter these limitations, use one of these alternatives:

1. **Full Linux System**: Run Kind on a bare metal Linux system or full VM (not containerized)

2. **Cloud Kubernetes**: Use a managed Kubernetes cluster:
   - Google Kubernetes Engine (GKE)
   - Amazon Elastic Kubernetes Service (EKS)
   - Azure Kubernetes Service (AKS)
   - DigitalOcean Kubernetes

3. **Local Alternatives**:
   - Minikube (may work better in restricted environments)
   - k3s/k3d (lightweight alternative)
   - MicroK8s

4. **Docker Desktop**: On macOS/Windows, Docker Desktop includes Kubernetes support

## Using Kind for HolmesGPT Testing

Once your Kind cluster is running, you can use it to test HolmesGPT:

```bash
# Deploy a test application
kubectl create deployment nginx --image=nginx
kubectl expose deployment nginx --port=80

# Run HolmesGPT against the cluster
poetry run holmes ask "What pods are running in the cluster?"

# Run LLM evaluation tests with Kubernetes
poetry run pytest tests/llm/ -k kubernetes --no-cov
```

## Cleanup

```bash
# Delete the cluster
kind delete cluster --name holmesgpt-test

# Stop Docker daemon (if started manually)
pkill dockerd
```

## Troubleshooting

### Docker daemon won't start
Check the logs:
```bash
cat /tmp/dockerd.log
```

### Kind cluster creation hangs
Increase timeout and check Docker:
```bash
docker ps
docker network ls
```

### kubectl can't connect
Verify the context:
```bash
kubectl config get-contexts
kubectl config use-context kind-holmesgpt-test
```

## Resources

- [Kind Documentation](https://kind.sigs.k8s.io/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [HolmesGPT Testing Guide](./CLAUDE.md#testing-framework)
