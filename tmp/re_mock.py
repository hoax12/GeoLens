import re
import os

path = r'geolens-app\lib\mockData.ts'

with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

# We need to manually do it, regex is too complex for this. I will just do it with multi_replace_file_content
