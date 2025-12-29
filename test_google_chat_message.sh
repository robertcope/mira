#!/bin/bash
# Test script for Google Chat webhook
# Simulates a MESSAGE event from Google Chat

PORT=${1:-1993}

echo "Testing Google Chat webhook on port $PORT..."
echo ""

# Test MESSAGE event
echo "Sending MESSAGE event..."
curl -X POST http://localhost:$PORT/v0/api/google-chat \
  -H "Content-Type: application/json" \
  -d '{
    "type": "MESSAGE",
    "message": {
      "text": "Hello MIRA, how are you today?",
      "sender": {
        "name": "users/12345678901234567890",
        "displayName": "Taylor",
        "email": "taylor@workspace.com",
        "type": "HUMAN"
      },
      "createTime": "2025-12-26T10:30:00.000000Z",
      "name": "spaces/AAAAAAAAAAA/messages/xyz123"
    },
    "user": {
      "name": "users/12345678901234567890",
      "displayName": "Taylor",
      "email": "taylor@workspace.com",
      "type": "HUMAN"
    },
    "space": {
      "name": "spaces/AAAAAAAAAAA",
      "type": "DM"
    },
    "token": "mock-bearer-token"
  }' 2>&1

echo ""
echo ""
echo "If you see a 'cardsV2' response above, the webhook is working!"
echo "If you see an error, check MIRA logs for details."
