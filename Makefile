PHONY : pack clean

pack:
	mkdir -p lambda-package
	cp homeless/lambda_handler.py lambda-package/lambda_handler.py
	pip install requests boto3 -t lambda-package
	cd lambda-package && zip --recurse-paths ../package.zip *

assets:
	wget https://releases.hashicorp.com/nomad/0.7.0/nomad_0.7.0_linux_amd64.zip -O nomad.zip && unzip nomad.zip
	mkdir assets && mv nomad assets/ && rm -f nomad.zip

clean:
	rm -f package.zip
	rm -rf lambda-package
