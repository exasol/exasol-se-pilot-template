#!/usr/bin/env bash
# Re-package + update both <PILOT_NAME> scheduler Lambda functions.
# Idempotent — safe to re-run after every code change to lambda_function.py.
set -euo pipefail

cd "$(dirname "$0")"

echo "→ packaging lambda_function.zip"
zip -q -FS lambda_function.zip lambda_function.py

for ACTION in start stop; do
    NAME="<PROJECT_SLUG>-$ACTION"
    echo "→ updating $NAME"
    aws lambda update-function-code \
        --function-name "$NAME" \
        --zip-file fileb://lambda_function.zip \
        --query '{name: FunctionName, sha: CodeSha256, size: CodeSize}' \
        --output table
    aws lambda wait function-updated --function-name "$NAME"
    if [[ "$ACTION" == "start" ]]; then
        echo "→ ensuring $NAME timeout is 900s for the XYZ-PILOT-156 warm-up gate"
        aws lambda update-function-configuration \
            --function-name "$NAME" \
            --timeout 900 \
            --query '{name: FunctionName, timeout: Timeout}' \
            --output table
        aws lambda wait function-updated --function-name "$NAME"
    fi
done

echo "done. Recent logs:"
echo "  aws logs tail /aws/lambda/<PROJECT_SLUG>-start --since 5m"
echo "  aws logs tail /aws/lambda/<PROJECT_SLUG>-stop  --since 5m"
