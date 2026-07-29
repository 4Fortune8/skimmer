.PHONY: redeploy redeploy-workflow

redeploy: redeploy-workflow

redeploy-workflow:
	./deploy/redeploy-workflow.sh
