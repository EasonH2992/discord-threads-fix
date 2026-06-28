# Use an official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Set environment variables (can be overridden by Unraid)
ENV DISCORD_TOKEN=your_token_here
ENV PYTHONUNBUFFERED=1

# Create logs dirs
RUN mkdir -p /app/logs/threads /app/logs/instagram

# Run the bot
CMD ["python", "-u", "bot.py"]
