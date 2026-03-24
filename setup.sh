#!/bin/bash

# ============================================================================
# SAFS v6.0 Setup Script
# ============================================================================
# Automated setup for SAFS development environment

set -e  # Exit on error

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "============================================"
echo "SAFS v6.0 Setup"
echo "============================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check Python version
echo "Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.10"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" = "$REQUIRED_VERSION" ]; then 
    echo -e "${GREEN}✓${NC} Python $PYTHON_VERSION found"
else
    echo -e "${RED}✗${NC} Python $REQUIRED_VERSION+ required (found $PYTHON_VERSION)"
    exit 1
fi

# Create virtual environment
echo ""
echo "Creating virtual environment..."
if [ -d "venv" ]; then
    echo -e "${YELLOW}⚠${NC} venv already exists, skipping..."
else
    python3 -m venv venv
    echo -e "${GREEN}✓${NC} Virtual environment created"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip --quiet
echo -e "${GREEN}✓${NC} pip upgraded"

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -e ".[dev]" --quiet
echo -e "${GREEN}✓${NC} Dependencies installed"

# Install Playwright browsers
echo ""
echo "Installing Playwright browsers..."
playwright install chromium --quiet
echo -e "${GREEN}✓${NC} Playwright browsers installed"

# Download NLTK data
echo ""
echo "Downloading NLTK data..."
python -c "import nltk; nltk.download('punkt', quiet=True); nltk.download('stopwords', quiet=True)"
echo -e "${GREEN}✓${NC} NLTK data downloaded"

# Copy .env.example to .env if not exists
echo ""
if [ -f ".env" ]; then
    echo -e "${YELLOW}⚠${NC} .env already exists, skipping..."
else
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo -e "${GREEN}✓${NC} .env created (please edit with your credentials)"
fi

# Check for Docker
echo ""
echo "Checking for Docker..."
if command -v docker &> /dev/null; then
    echo -e "${GREEN}✓${NC} Docker found"
    
    # Ask if user wants to start services
    read -p "Start Docker services (PostgreSQL, Redis, Qdrant)? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Starting Docker services..."
        # TODO: Add docker-compose.yml and start services
        echo -e "${YELLOW}⚠${NC} docker-compose.yml not yet created (future phase)"
    fi
else
    echo -e "${YELLOW}⚠${NC} Docker not found (optional, but recommended)"
fi

# Check for QEMU
echo ""
echo "Checking for QEMU..."
if command -v qemu-arm-static &> /dev/null; then
    echo -e "${GREEN}✓${NC} QEMU found"
else
    echo -e "${YELLOW}⚠${NC} QEMU not found (required for PATH α validation)"
    echo "Install on macOS: brew install qemu"
    echo "Install on Linux: apt-get install qemu-user-static"
fi

# Check for POC projects
echo ""
echo "Checking for POC projects..."
POC_BASE="../"

if [ -d "${POC_BASE}mcp_server_jira_log_analyzer" ]; then
    echo -e "${GREEN}✓${NC} mcp_server_jira_log_analyzer found"
else
    echo -e "${YELLOW}⚠${NC} mcp_server_jira_log_analyzer not found at ${POC_BASE}"
fi

if [ -d "${POC_BASE}mcp_tv_controller/vizio-mcp" ]; then
    echo -e "${GREEN}✓${NC} vizio-mcp found"
else
    echo -e "${YELLOW}⚠${NC} vizio-mcp not found at ${POC_BASE}mcp_tv_controller/"
fi

if [ -d "${POC_BASE}mcp-second-screen/jira_auto_fixer" ]; then
    echo -e "${GREEN}✓${NC} jira_auto_fixer found"
else
    echo -e "${YELLOW}⚠${NC} jira_auto_fixer not found at ${POC_BASE}mcp-second-screen/"
fi

# Summary
echo ""
echo "============================================"
echo "Setup Complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "1. Activate virtual environment:"
echo "   source venv/bin/activate"
echo ""
echo "2. Edit .env with your credentials:"
echo "   - ANTHROPIC_API_KEY"
echo "   - JIRA_API_TOKEN"
echo "   - GITHUB_TOKEN"
echo "   - VOYAGE_API_KEY"
echo ""
echo "3. Run tests:"
echo "   pytest tests/unit -v"
echo ""
echo "4. Check status:"
echo "   safs status"
echo ""
echo "5. See README.md for full documentation"
echo ""
echo -e "${GREEN}Happy coding!${NC}"
