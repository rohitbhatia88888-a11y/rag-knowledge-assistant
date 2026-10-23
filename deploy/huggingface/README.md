---
title: RAG Knowledge Assistant
emoji: 📚
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Permission-aware RAG with cited answers and an eval gate
---

# RAG Knowledge Assistant: public demo

A chat assistant over the documents of **Halcyon Robotics**, a fictional company. Pick a persona: each one retrieves only the documents its role may read, and every answer cites the page and passage it came from.

Source code, architecture and evaluation results: https://github.com/rohitbhatia88888-a11y/rag-knowledge-assistant

This Space is built from that repository with `deploy/huggingface/deploy.py`. Questions are rate-limited per visitor and per day.
