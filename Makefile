SHELL=/bin/bash

pkg_name=gcalvault
pkg_version:=$(shell cat src/VERSION.txt | xargs)
cli_name:=${pkg_name}

container_hub_acct=rtomac
image_name:=${pkg_name}
image_tag=latest
image_version_tag:=${pkg_version}
image_platforms=linux/amd64,linux/arm64,linux/arm/v7,linux/arm/v6

all: build

.PHONY: devenv
devenv:
	[ ! -d "./.devenv" ] && virtualenv .devenv || true
	. ./.devenv/bin/activate && pip install '.[dev,test,release]'

.PHONY: dist
dist:
	python3 setup.py sdist
	ln -f "dist/${pkg_name}-${pkg_version}.tar.gz" "dist/${pkg_name}-latest.tar.gz"

.PHONY: build
build: dist
	docker build \
		-t ${image_name}:local \
		.

.PHONY: test
test: build
	docker run -it --rm \
		-v ${PWD}/.conf:/root/.${pkg_name} \
		-v ${PWD}/.output:/root/${pkg_name} \
		-v ${PWD}:/usr/local/src/${pkg_name} \
		--workdir /usr/local/src/${pkg_name} \
		--entrypoint pytest \
		${image_name}:local

.PHONY: debug
debug: build
	docker run -it --rm \
		-v ${PWD}/.conf:/root/.${pkg_name} \
		-v ${PWD}/.output:/root/${pkg_name} \
		-v ${PWD}/bin/${cli_name}:/usr/local/bin/${cli_name} \
		-v ${PWD}/src:/usr/local/lib/python3.9/site-packages/${pkg_name} \
		-v ${PWD}/tests:/usr/local/src/${pkg_name}/tests \
		--workdir /usr/local/src/${pkg_name} \
		--entrypoint /bin/bash \
		${image_name}:local

user=foo.bar@gmail.com
.PHONY: run
run: build
	docker run -it --rm \
		-v ${PWD}/.conf:/root/.${pkg_name} \
		-v ${PWD}/.output:/root/${pkg_name} \
		${image_name}:local sync ${user}

.PHONY: release
release: test
	twine upload --repository testpypi dist/${pkg_name}-${pkg_version}.tar.gz
	
	twine upload dist/${pkg_name}-${pkg_version}.tar.gz

	docker buildx build \
		--tag "${container_hub_acct}/${image_name}:${image_tag}" \
		--tag "${container_hub_acct}/${image_name}:${image_version_tag}" \
		--platform "${image_platforms}" \
		--push \
		.
