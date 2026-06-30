---
name: 3D-Model-Generator
description: Generates 3D models from text descriptions or photos using Tencent Hunyuan AI. Supports STL (for 3D printing) and GLB (for games). Use when a user needs a 3D model created quickly.
---

# 3D Model Generator Skill

## Overview
This skill allows AI agents to generate 3D models from text descriptions or photos. It uses the Tencent Hunyuan 3D AI API to create printable or game-ready models in STL and GLB formats.

## When to Use
- When a user requests a 3D model for 3D printing or game development.
- When a user wants to visualize an object from a description or a photo.
- When a user needs a quick prototype or concept model.

## Capabilities
- Generate 3D models from **text descriptions** (e.g., "a simple wooden chair with four legs").
- Generate 3D models from **photos** (upload any image).
- Output formats: **STL** (for 3D printing, geometry only) and **GLB** (for games and editors, with colors/textures).
- Models are generated within 2-5 minutes.

## How to Use This Skill

### Step 1: Start the Bot
Open Telegram and send `/start` to **@Kostya_3d_bot**.

### Step 2: Choose Generation Type
- For text descriptions, press the button **"🔧 Генерация по тексту"**.
- For photos, press the button **"📸 Генерация по фото"**.

### Step 3: Select Output Format
Press **"📦 Формат"** and choose between:
- **STL** — for 3D printing. This format contains only geometry, no color.
- **GLB** — for games and editors. This format includes textures and color.

### Step 4: Send Your Request
- If you chose **text generation**: send a detailed description of the object you want to create.
- If you chose **photo generation**: send a photo of the object you want to recreate in 3D.

### Step 5: Wait for the Model
The bot will process your request and return the model as a downloadable file. The generation usually takes 2-5 minutes.

## Technical Details
- **API**: Tencent Hunyuan 3D (Model 3.1)
- **Supported Formats**: STL, GLB
- **Max Polygons**: 1,000,000
- **Features**: PBR Textures, Auto-Conversion

## Example Commands

### Text Generation Example
For a user request like *"Generate a 3D model of a gear with 73 teeth, 61mm diameter, 15mm height"*, the agent should instruct the user to send this exact text to the bot.

### Photo Generation Example
For a user request like *"Create a 3D model from this photo of a chair"*, the agent should instruct the user to send the photo to the bot.

## Limitations
- Generation can take up to 2-5 minutes.
- The STL format does not preserve colors or textures.
- Maximum file size: ~50 MB.

## Important Notes
- The bot uses Telegram Stars for payment after the free limit is exhausted. Ensure the user has Telegram Stars if needed.
- The bot supports both Russian and English interfaces.

## Links
- **Bot on Telegram**: [@Kostya_3d_bot](https://t.me/Kostya_3d_bot)
- **Source Code**: [https://github.com/hevron16/3d-bot](https://github.com/hevron16/3d-bot)
