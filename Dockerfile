# base layer
FROM python:3.10-slim

# working dir
WORKDIR /app

# copy requirements file
COPY requirements.txt .

# installing dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# copying all the files
COPY . .

# exposing the ports
EXPOSE 8501

# cmd to run the file
CMD ["streamlit","run","main.py"]