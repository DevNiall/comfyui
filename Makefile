# ComfyUI Simple — Makefile
# All operations for deploying, managing, and connecting to ComfyUI on AWS.

SHELL := /bin/bash
.DEFAULT_GOAL := help

STACK_NAME     := ComfyUISimpleStack
HF_TOKEN_PARAM := /comfyui/hf-token
LOCAL_PORT     := 8181
REMOTE_PORT    := 8181

# Resolve region and profile
REGION      := $(or $(AWS_DEFAULT_REGION),$(shell aws configure get region 2>/dev/null),eu-west-2)
AWS_PROFILE := $(or $(AWS_PROFILE),default)
$(info AWS Profile : $(AWS_PROFILE)  |  Region : $(REGION))

# Dynamic lookups (lazy evaluation)
ASG_NAME = $(shell aws cloudformation describe-stacks \
	--stack-name $(STACK_NAME) \
	--query 'Stacks[0].Outputs[?OutputKey==`ASGName`].OutputValue' \
	--output text --region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null)

STACK_TAG = $(shell aws cloudformation describe-stacks \
	--stack-name $(STACK_NAME) \
	--query 'Stacks[0].Outputs[?OutputKey==`StackTag`].OutputValue' \
	--output text --region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null)

INSTANCE_ID = $(shell aws ec2 describe-instances \
	--filters "Name=tag:Name,Values=ComfyUI-Host" \
	          "Name=instance-state-name,Values=running" \
	--query 'Reservations[].Instances[].InstanceId' \
	--output text --region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null)

VOLUME_ID = $(shell aws ec2 describe-volumes \
	--filters "Name=tag:Name,Values=comfyui-data" \
	          "Name=tag:comfyui-stack,Values=$(STACK_TAG)" \
	          "Name=status,Values=in-use" \
	--query 'Volumes[0].VolumeId' \
	--output text --region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null)

# ============================================================================
# CDK Operations
# ============================================================================

.PHONY: ensure-hf-token
ensure-hf-token: ## Check HF token SSM parameter exists (prerequisite for deploy)
	@RESULT=$$(aws ssm get-parameter --name "$(HF_TOKEN_PARAM)" \
		--query 'Parameter.Name' --output text --region $(REGION) --profile $(AWS_PROFILE) 2>&1); \
	EXIT=$$?; \
	if [ $$EXIT -eq 0 ]; then \
		echo "✅ HuggingFace token found at $(HF_TOKEN_PARAM)"; \
	elif echo "$$RESULT" | grep -q "ParameterNotFound"; then \
		echo "❌ HuggingFace token not set. Run 'make set-hf-token' before deploying."; \
		exit 1; \
	else \
		echo "❌ AWS authentication failed — cannot verify HF token. Check credentials for profile '$(AWS_PROFILE)'."; \
		echo "   $$RESULT"; \
		exit 1; \
	fi

.PHONY: deploy
deploy: ensure-hf-token ## Deploy the ComfyUI stack (requires HF token — run set-hf-token first)
	@echo "🚀 Deploying $(STACK_NAME)..."
	npx cdk deploy --require-approval never

.PHONY: destroy
destroy: ## Destroy the ComfyUI stack (data volumes/snapshots preserved)
	@echo "💥 Destroying $(STACK_NAME)..."
	npx cdk destroy --force

.PHONY: synth
synth: ## Synthesize the CloudFormation template
	npx cdk synth

.PHONY: diff
diff: ## Show pending infrastructure changes
	npx cdk diff

.PHONY: bootstrap
bootstrap: ## Bootstrap CDK in your AWS account/region
	npx cdk bootstrap

# ============================================================================
# Instance Lifecycle
# ============================================================================

.PHONY: start
start: ## Start the ComfyUI instance (set ASG desired=1)
	@echo "▶️  Starting ComfyUI instance..."
	aws autoscaling set-desired-capacity \
		--auto-scaling-group-name "$(ASG_NAME)" \
		--desired-capacity 1 \
		--region $(REGION) --profile $(AWS_PROFILE)
	@echo "Instance launching. Run 'make status' to check progress."

.PHONY: stop
stop: ## Stop the ComfyUI instance (set ASG desired=0, triggers snapshot)
	@echo "⏹️  Stopping ComfyUI instance (snapshot will be created)..."
	aws autoscaling set-desired-capacity \
		--auto-scaling-group-name "$(ASG_NAME)" \
		--desired-capacity 0 \
		--region $(REGION) --profile $(AWS_PROFILE)
	@echo "Instance terminating. Lifecycle hook will snapshot data volume."

.PHONY: status
status: ## Show instance, ASG, and snapshot status
	@echo "=== ASG Status ==="
	@aws autoscaling describe-auto-scaling-groups \
		--auto-scaling-group-names "$(ASG_NAME)" \
		--query 'AutoScalingGroups[0].{DesiredCapacity:DesiredCapacity,Instances:Instances[].{Id:InstanceId,State:LifecycleState,Health:HealthStatus}}' \
		--output table --region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null || echo "ASG not found"
	@echo ""
	@echo "=== EC2 Instance ==="
	@aws ec2 describe-instances \
		--filters "Name=tag:Name,Values=ComfyUI-Host" \
		--query 'Reservations[].Instances[].{Id:InstanceId,State:State.Name,Type:InstanceType,AZ:Placement.AvailabilityZone,LaunchTime:LaunchTime}' \
		--output table --region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null || echo "No instances"
	@echo ""
	@echo "=== Latest Snapshot ==="
	@aws ec2 describe-snapshots \
		--filters "Name=tag:comfyui-stack,Values=$(STACK_TAG)" \
		--owner-ids self \
		--query 'sort_by(Snapshots,&StartTime)[-1].{Id:SnapshotId,State:State,StartTime:StartTime,Size:VolumeSize,CreatedBy:Tags[?Key==`CreatedBy`].Value|[0]}' \
		--output table --region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null || echo "No snapshots"

# ============================================================================
# Connectivity
# ============================================================================

.PHONY: connect
connect: ## Open a shell on the EC2 host via SSM Session Manager
	@if [ -z "$(INSTANCE_ID)" ]; then \
		echo "❌ No running ComfyUI instance found. Run 'make start' first."; \
		exit 1; \
	fi
	@echo "🔗 Connecting to instance $(INSTANCE_ID)..."
	aws ssm start-session \
		--target "$(INSTANCE_ID)" \
		--region $(REGION) --profile $(AWS_PROFILE)

.PHONY: comfyui
comfyui: ## Port-forward ComfyUI (localhost:8181) via SSM
	@if [ -z "$(INSTANCE_ID)" ]; then \
		echo "❌ No running ComfyUI instance found. Run 'make start' first."; \
		exit 1; \
	fi
	@echo "🌐 Port-forwarding ComfyUI on http://localhost:$(LOCAL_PORT)"
	@echo "   Press Ctrl+C to disconnect."
	aws ssm start-session \
		--target "$(INSTANCE_ID)" \
		--document-name AWS-StartPortForwardingSession \
		--parameters '{"portNumber":["$(REMOTE_PORT)"],"localPortNumber":["$(LOCAL_PORT)"]}' \
		--region $(REGION) --profile $(AWS_PROFILE)

.PHONY: connect-container
connect-container: ## Open a shell inside the ComfyUI Docker container via SSM
	@if [ -z "$(INSTANCE_ID)" ]; then \
		echo "❌ No running ComfyUI instance found. Run 'make start' first."; \
		exit 1; \
	fi
	@echo "🐳 Opening shell in ComfyUI container..."
	aws ssm start-session \
		--target "$(INSTANCE_ID)" \
		--document-name AWS-StartInteractiveCommand \
		--parameters '{"command":["sudo docker exec -it comfyui /bin/bash"]}' \
		--region $(REGION) --profile $(AWS_PROFILE)

.PHONY: logs
logs: ## Tail ComfyUI container logs (works even if container has exited)
	@if [ -z "$(INSTANCE_ID)" ]; then \
		echo "❌ No running ComfyUI instance found. Run 'make start' first."; \
		exit 1; \
	fi
	@echo "📋 ComfyUI container logs (Ctrl+C to stop if still running)..."
	aws ssm start-session \
		--target "$(INSTANCE_ID)" \
		--document-name AWS-StartInteractiveCommand \
		--parameters '{"command":["sudo docker logs --tail 200 -f comfyui 2>&1 || echo \"Container not found or never started\""]}' \
		--region $(REGION) --profile $(AWS_PROFILE)

.PHONY: diagnose
diagnose: ## Show full boot diagnostics: bootstrap log + docker state + cloud-init
	@if [ -z "$(INSTANCE_ID)" ]; then \
		echo "❌ No running ComfyUI instance found. Run 'make start' first."; \
		exit 1; \
	fi
	@echo "🔍 Running diagnostics on $(INSTANCE_ID)..."
	aws ssm start-session \
		--target "$(INSTANCE_ID)" \
		--document-name AWS-StartInteractiveCommand \
		--parameters '{"command":["bash -c \"echo \\\"=== DOCKER CONTAINERS (all) ===\\\" && sudo docker ps -a --format \\\"table {{.Names}}\\t{{.Status}}\\t{{.Image}}\\\" 2>/dev/null || echo no-docker; echo; echo \\\"=== MOUNTS ===\\\" && mount | grep /data || echo not-mounted; echo; echo \\\"=== BOOTSTRAP LOG ===\\\" && cat /var/log/comfyui-bootstrap.log 2>/dev/null || echo log-not-found; echo; echo \\\"=== CLOUD-INIT OUTPUT (last 40 lines) ===\\\" && tail -40 /var/log/cloud-init-output.log 2>/dev/null || echo no-cloud-init-log\""]}' \
		--region $(REGION) --profile $(AWS_PROFILE)

.PHONY: bootstrap-log
bootstrap-log: ## View the EC2 bootstrap log
	@if [ -z "$(INSTANCE_ID)" ]; then \
		echo "❌ No running ComfyUI instance found. Run 'make start' first."; \
		exit 1; \
	fi
	@echo "📋 Fetching bootstrap log..."
	aws ssm start-session \
		--target "$(INSTANCE_ID)" \
		--document-name AWS-StartInteractiveCommand \
		--parameters '{"command":["cat /var/log/comfyui-bootstrap.log"]}' \
		--region $(REGION) --profile $(AWS_PROFILE)

# ============================================================================
# Snapshots
# ============================================================================

.PHONY: snapshot
snapshot: ## Create a manual EBS snapshot of the data volume right now
	@if [ -z "$(VOLUME_ID)" ] || [ "$(VOLUME_ID)" = "None" ]; then \
		echo "❌ No comfyui-data volume found. Is the instance running?"; \
		exit 1; \
	fi
	@echo "📸 Creating snapshot of volume $(VOLUME_ID)..."
	@SNAP_ID=$$(aws ec2 create-snapshot \
		--volume-id "$(VOLUME_ID)" \
		--description "ComfyUI manual snapshot - $$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		--tag-specifications "ResourceType=snapshot,Tags=[{Key=Name,Value=comfyui-data},{Key=comfyui-stack,Value=$(STACK_TAG)},{Key=CreatedBy,Value=manual},{Key=CreatedAt,Value=$$(date -u +%Y-%m-%dT%H:%M:%SZ)}]" \
		--query 'SnapshotId' --output text \
		--region $(REGION) --profile $(AWS_PROFILE)) && \
	echo "✅ Snapshot initiated: $$SNAP_ID" && \
	echo "   Snapshot will complete in the background."

.PHONY: list-snapshots
list-snapshots: ## List all ComfyUI data snapshots
	@echo "=== ComfyUI Snapshots ==="
	@aws ec2 describe-snapshots \
		--filters "Name=tag:comfyui-stack,Values=$(STACK_TAG)" \
		--owner-ids self \
		--query 'sort_by(Snapshots,&StartTime)[].{Id:SnapshotId,State:State,Started:StartTime,Size:VolumeSize,CreatedBy:Tags[?Key==`CreatedBy`].Value|[0],Progress:Progress}' \
		--output table --region $(REGION) --profile $(AWS_PROFILE)

# ============================================================================
# HuggingFace Token
# ============================================================================

.PHONY: set-hf-token
set-hf-token: ## Set your HuggingFace API token (stored encrypted in SSM)
	@read -sp "Enter your HuggingFace token: " HF_TOKEN && echo && \
	aws ssm put-parameter \
		--name "$(HF_TOKEN_PARAM)" \
		--value "$$HF_TOKEN" \
		--type SecureString \
		--overwrite \
		--region $(REGION) --profile $(AWS_PROFILE) > /dev/null && \
	echo "✅ HuggingFace token stored at $(HF_TOKEN_PARAM)" && \
	echo "   Restart the instance ('make stop && make start') to pick up the new token."

.PHONY: get-hf-token
get-hf-token: ## Show whether HF token is configured (masked)
	@TOKEN=$$(aws ssm get-parameter \
		--name "$(HF_TOKEN_PARAM)" \
		--with-decryption \
		--query 'Parameter.Value' --output text \
		--region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null || echo "not-found") && \
	if [ "$$TOKEN" = "not-set" ] || [ "$$TOKEN" = "not-found" ]; then \
		echo "❌ HuggingFace token not configured. Run 'make set-hf-token'."; \
	else \
		echo "✅ HuggingFace token is configured ($${TOKEN:0:4}****)"; \
	fi

# ============================================================================
# Cleanup
# ============================================================================

.PHONY: delete-snapshots
delete-snapshots: ## Delete ALL ComfyUI snapshots (interactive confirmation)
	@echo "⚠️  This will delete ALL ComfyUI snapshots!"
	@read -p "Type 'yes' to confirm: " CONFIRM && \
	if [ "$$CONFIRM" = "yes" ]; then \
		aws ec2 describe-snapshots \
			--filters "Name=tag:comfyui-stack,Values=$(STACK_TAG)" \
			--owner-ids self \
			--query 'Snapshots[].SnapshotId' --output text \
			--region $(REGION) --profile $(AWS_PROFILE) | tr '\t' '\n' | while read SNAP; do \
				echo "Deleting $$SNAP..."; \
				aws ec2 delete-snapshot --snapshot-id "$$SNAP" --region $(REGION) --profile $(AWS_PROFILE) 2>/dev/null || true; \
			done; \
		echo "✅ All snapshots deleted."; \
	else \
		echo "Cancelled."; \
	fi

.PHONY: delete-volumes
delete-volumes: ## Delete orphaned comfyui-data volumes (available/not attached)
	@echo "Checking for orphaned comfyui-data volumes..."
	@aws ec2 describe-volumes \
		--filters "Name=tag:Name,Values=comfyui-data" \
		          "Name=tag:comfyui-stack,Values=$(STACK_TAG)" \
		          "Name=status,Values=available" \
		--query 'Volumes[].VolumeId' --output text \
		--region $(REGION) --profile $(AWS_PROFILE) | tr '\t' '\n' | while read VOL; do \
			if [ -n "$$VOL" ] && [ "$$VOL" != "None" ]; then \
				echo "Deleting orphaned volume $$VOL..."; \
				aws ec2 delete-volume --volume-id "$$VOL" --region $(REGION) --profile $(AWS_PROFILE); \
			fi; \
		done
	@echo "Done."

.PHONY: nuke
nuke: delete-snapshots delete-volumes destroy ## Full teardown: snapshots + volumes + stack
	@echo "🔥 Everything destroyed."

# ============================================================================
# Help
# ============================================================================

.PHONY: help
help: ## Show this help
	@echo "ComfyUI Simple — AWS Deployment"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
