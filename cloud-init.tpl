#cloud-config
packages:
  - python3-venv
  - git

write_files:
  - path: /tmp/bench.env
    permissions: '0600'
    owner: root:root
    content: |
      AZURE_AI_ENDPOINT=${ai_endpoint}
      DEPLOYMENT_DEFAULT=${deployment_default}
      DEPLOYMENT_STRICT=${deployment_strict}
      DEPLOYMENT_PRISMA=${deployment_prisma}
      PRISMA_AIRS_API_KEY=${prisma_airs_api_key}
      PRISMA_AIRS_PROFILE_NAME=ai-foundry-prisma-benchmark
      PRISMA_AIRS_DIRECT_API_KEY=${prisma_airs_direct_api_key}
      PRISMA_AIRS_DIRECT_PROFILE_NAME=bench-direct-api

runcmd:
  - git clone https://github.com/thresh97/azure-foundry-guardrail-latency /home/azureuser/bench
  - cd /home/azureuser/bench && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
  - mv /tmp/bench.env /home/azureuser/bench/.env
  - chown -R azureuser:azureuser /home/azureuser/bench
  - chmod 600 /home/azureuser/bench/.env
