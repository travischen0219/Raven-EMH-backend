DOTENV_FILE=.env.staging
PAPERSPACE_GTADIENT_SPEC_FILE=gradient-deployment.yaml

-include $(DOTENV_FILE)

run:
	CUDA_VISIBLE_DEVICES=0 python3 main.py

staging:
	CUDA_VISIBLE_DEVICES=0 ENV=staging python3 main.py

dev:
	CUDA_VISIBLE_DEVICES=0 ENV=dev python3 main.py

docker_build:
	docker build -t $(PAPERSPACE_DEPLOY_IMAGE) .

docker_run:
	docker run -it \
		-p $(APP_PORT):$(APP_PORT) \
		--env-file $(DOTENV_FILE) \
		$(PAPERSPACE_DEPLOY_IMAGE)

docker_run_gpus:
	docker run --gpus all -it \
		-p $(APP_PORT):$(APP_PORT) \
		--env-file $(DOTENV_FILE) \
		$(PAPERSPACE_DEPLOY_IMAGE)

docker_run_bash:
	docker run -it --rm \
		-p $(APP_PORT):$(APP_PORT) \
		--env-file $(DOTENV_FILE) \
		--entrypoint /bin/bash \
		$(PAPERSPACE_DEPLOY_IMAGE)

docker_run_bash_gpus:
	docker run --gpus all -it \
		-p $(APP_PORT):$(APP_PORT) \
		--env-file $(DOTENV_FILE) \
		--entrypoint /bin/bash \
		$(PAPERSPACE_DEPLOY_IMAGE)

generate_gradient_spec:
	bash scripts/make_spec_from_env.sh -e $(DOTENV_FILE) -o $(PAPERSPACE_GTADIENT_SPEC_FILE)

create_gradient_deployment: docker_build
	docker push $(PAPERSPACE_DEPLOY_IMAGE)
	gradient deployments create \
		--apiKey $(PAPERSPACE_API_KEY) \
		--projectId $(PAPERSPACE_DEPLOY_PROJECT_ID) \
		--name $(APP_NAME)\
		--spec $(PAPERSPACE_GTADIENT_SPEC_FILE)
