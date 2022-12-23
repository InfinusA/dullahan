.DEFAULT_GOAL := build

build:
	mkdir -p build
	python3 -m build --outdir build
install: 
	pip install --force-reinstall build/dullahan-0.0.*.tar.gz
#todo: dynamic version number
clean:
	rm -r build

full: clean build install
	echo "Clean, build, install complete"
