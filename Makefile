.DEFAULT_GOAL := build

build:
	mkdir -p build
	python3 -m build --outdir build
install: 
	pip install build/dullahan-0.0.*.tar.gz
#todo: dynamic version number
clean:
	rm -r build
remove:
	pip uninstall dullahan

full: clean build remove install
	echo "Clean, build, install complete"
