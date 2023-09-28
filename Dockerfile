# Use the official Python 3.10 image as the base image
FROM python:3.10

RUN apt-get update && \
    apt-get install -y vim expect
    # Set the working directory inside the container
WORKDIR /app

# Copy the contents of the local "app" directory to the container's working directory
COPY . .
# Set the environment variable within the Dockerfile

# Run the install.sh script inside the container
RUN pip install .
RUN pip install -r requirements.txt
#RUN python -c "import telliot_core; print(f'telliot-core version installed - {telliot_core.__version__}')"
#RUN python -c "import telliot_feeds; print(f'telliot-feeds version installed - {telliot_feeds.__version__}')"
#RUN /usr/local/lib/python3.10/site-packages/telliot_core/data/contract_directory.dev.json /usr/local/lib/python3.10/site-packages/telliot_core/data/contract_directory.json

# Specify the command to run when the container starts
#CMD [ "bash", "runtime.sh" ] #Uncomment this line if you want to test locally