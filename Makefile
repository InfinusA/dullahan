.DEFAULT_GOAL := build

build:
	mkdir -p build
	python3 -m build --outdir build
install: 
	pip install --break-system-packages build/dullahan-0.0.*.tar.gz
#todo: dynamic version number
clean:
	rm -rf build
remove:
	pip uninstall --break-system-packages dullahan

full: clean build remove install
	echo "Clean, build, install complete"
