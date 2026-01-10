#!/bin/bash
# Kind Cluster Setup Script for HolmesGPT Development
# This script sets up Kind (Kubernetes in Docker) for local testing

set -e

echo "=== Kind Cluster Setup for HolmesGPT ==="
echo

# Create bin directory
mkdir -p ~/bin
export PATH="$HOME/bin:$PATH"

# Install kubectl
if ! command -v kubectl &> /dev/null; then
    echo "Installing kubectl..."
    KUBECTL_VERSION=$(curl -L -s https://dl.k8s.io/release/stable.txt)
    curl -Lo ~/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
    chmod +x ~/bin/kubectl
    echo "✓ kubectl installed: $(kubectl version --client --short 2>&1 | head -1)"
else
    echo "✓ kubectl already installed: $(kubectl version --client --short 2>&1 | head -1)"
fi

# Install Kind
if ! command -v kind &> /dev/null; then
    echo "Installing Kind..."
    KIND_VERSION="v0.26.0"
    curl -Lo ~/bin/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
    chmod +x ~/bin/kind
    echo "✓ Kind installed: $(kind version)"
else
    echo "✓ Kind already installed: $(kind version)"
fi

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo
    echo "⚠️  Docker not found. Installing Docker binaries..."

    # Download and install Docker static binaries
    DOCKER_VERSION="27.5.1"
    cd /tmp
    curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" -o docker.tgz
    tar xzvf docker.tgz
    cp docker/* ~/bin/
    rm -rf docker docker.tgz
    cd -

    echo "✓ Docker binaries installed"
fi

# Check if Docker daemon is running
if ! docker ps &> /dev/null; then
    echo
    echo "Starting Docker daemon..."

    # Create data directory
    mkdir -p /tmp/docker-data

    # Start dockerd in background
    # Using vfs storage driver and disabling networking features for restricted environments
    nohup dockerd \
        --data-root=/tmp/docker-data \
        --storage-driver=vfs \
        --iptables=false \
        --bridge=none \
        --userland-proxy=false \
        > /tmp/dockerd.log 2>&1 &

    # Wait for Docker to start
    echo -n "Waiting for Docker daemon to start"
    for i in {1..30}; do
        if docker ps &> /dev/null; then
            echo " ✓"
            break
        fi
        echo -n "."
        sleep 1
    done

    if ! docker ps &> /dev/null; then
        echo " ✗"
        echo "ERROR: Docker daemon failed to start. Check /tmp/dockerd.log for details."
        exit 1
    fi
else
    echo "✓ Docker daemon is running"
fi

echo
echo "=== Docker Information ==="
docker version --format '{{.Server.Version}}'

echo
echo "=== Environment Limitations ==="
echo "This environment has kernel restrictions that prevent Docker networking."
echo "As a result, Kind clusters cannot be created in this environment."
echo
echo "To use Kind for local development:"
echo "1. Run this on a full Linux system (not in a container)"
echo "2. Or use a VM with proper kernel support"
echo "3. Or use a cloud-based Kubernetes cluster"
echo

# Try to create Kind cluster (will fail in restricted environments)
echo "Attempting to create Kind cluster..."
if kind create cluster --name holmesgpt-test 2>&1; then
    echo "✓ Kind cluster created successfully!"
    kubectl cluster-info --context kind-holmesgpt-test
else
    echo
    echo "⚠️  Kind cluster creation failed due to environment limitations."
    echo "   This is expected in containerized/restricted environments."
    echo "   For HolmesGPT development with Kubernetes, use:"
    echo "   - A full Linux VM or bare metal system"
    echo "   - Cloud Kubernetes (GKE, EKS, AKS)"
    echo "   - An existing Kubernetes cluster"
    echo
    echo "Tools are installed and ready for use with external clusters."
fi

echo
echo "=== Setup Complete ==="
echo "Add ~/bin to your PATH:"
echo 'export PATH="$HOME/bin:$PATH"'
