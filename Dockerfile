# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Create a non-root user and group
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin -c "Docker image user" appuser

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Create directories for data and temporary files and set permissions
RUN mkdir -p data/temp && \
    chown -R appuser:appuser data && \
    chmod -R 755 data

# Switch to the non-root user
USER appuser

# Tune glibc to aggressively return freed memory to the OS.
# MALLOC_TRIM_THRESHOLD_: Trim heap when 64KB+ free space accumulates.
# MALLOC_MMAP_THRESHOLD_: Allocations >128KB use mmap (freed instantly on free()).
ENV MALLOC_TRIM_THRESHOLD_=65536
ENV MALLOC_MMAP_THRESHOLD_=131072

# Run main.py when the container launches
CMD ["python", "main.py"]