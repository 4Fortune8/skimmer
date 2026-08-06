.PHONY: redeploy redeploy-workflow lid-model backfill-languages

redeploy: redeploy-workflow

redeploy-workflow:
	./deploy/redeploy-workflow.sh

# Fetch the fastText language model used when YouTube reports no language tag.
lid-model:
	python -c "from skimmer.domain import language; print(language.download_model())"

# Sample untagged videos and store the language tags YouTube already knows.
backfill-languages:
	python -m skimmer.collectors.video_languages
