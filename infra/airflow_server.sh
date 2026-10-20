#!/bin/bash
# Day-to-day commands for the Airflow EC2 server, all through AWS SSM (no SSH, no open ports).
#   ./infra/airflow_server.sh ui       forward the Airflow UI to http://localhost:8080 (needs session-manager-plugin)
#   ./infra/airflow_server.sh deploy   pull main on the server and restart Airflow (rebuilds the image if needed)
#   ./infra/airflow_server.sh status   service state, containers and memory
set -euo pipefail
REGION=ap-southeast-2
ID=$(terraform -chdir="$(dirname "$0")" output -raw airflow_instance_id)

run() {  # run shell commands on the server and print their output
  local cmd_id
  cmd_id=$(aws ssm send-command --region $REGION --instance-ids "$ID" --document-name AWS-RunShellScript \
    --parameters "commands=[\"$1\"]" --query Command.CommandId --output text)
  aws ssm wait command-executed --region $REGION --command-id "$cmd_id" --instance-id "$ID" 2>/dev/null || true
  aws ssm get-command-invocation --region $REGION --command-id "$cmd_id" --instance-id "$ID" \
    --query StandardOutputContent --output text
}

case "${1:-}" in
  ui)
    aws ssm start-session --region $REGION --target "$ID" --document-name AWS-StartPortForwardingSession \
      --parameters portNumber=8080,localPortNumber=8080 ;;
  deploy)
    run "sudo -u ec2-user git -C /home/ec2-user/vehicle-safety-pipeline pull -q && sudo -u ec2-user git -C /home/ec2-user/vehicle-safety-pipeline log --oneline -1 && systemctl restart --no-block vsp-airflow && echo restarting"
    echo "Airflow restarts in the background; DAGs are unpaused once it's up (a few minutes)." ;;
  status)
    run "systemctl is-active vsp-airflow; docker ps --format '{{.Names}} {{.Status}}'; free -m | head -2" ;;
  *)
    sed -n 2,5p "$0"; exit 1 ;;
esac
