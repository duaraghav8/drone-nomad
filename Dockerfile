from python:3
add . /tmp/homeless
run pip install /tmp/homeless && rm -rf /tmp/homeless
entrypoint ["python", "-m", "homeless.main"]