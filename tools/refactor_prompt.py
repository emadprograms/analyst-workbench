import re

with open("modules/ai/ai_services.py", "r") as f:
    content = f.read()

# Refactor company card prompt
prompt_start = content.find("    prompt = f\"\"\"\n    [Raw Market Context for Today]")
if prompt_start != -1:
    prompt_end = content.find("    \"\"\"\n    \n    logger.log(f\"3. Calling EOD AI Analyst", prompt_start)
    if prompt_end != -1:
        prompt_block = content[prompt_start:prompt_end]
        
        # Split into data and instructions
        # Data block ends before [Your Task for {trade_date_str}]
        task_idx = prompt_block.find("    [Your Task for {trade_date_str}]")
        
        data_block = prompt_block[18:task_idx].strip()
        instructions_block = prompt_block[task_idx:].strip()
        
        new_prompt = f'''    prompt = f"""
    {instructions_block}
    
    --- START OF DATA ---
    
    {data_block}
    
    --- END OF DATA ---
    Begin your JSON output now.'''
        
        content = content[:prompt_start] + new_prompt + content[prompt_end:]

# Refactor economy card prompt
econ_prompt_start = content.find("    prompt = f\"\"\"\n    [Previous Day's Economy Card (Read-Only)]")
if econ_prompt_start != -1:
    econ_prompt_end = content.find("    \"\"\"\n\n    logger.log(\"3. Calling Macro Strategist AI...\")", econ_prompt_start)
    if econ_prompt_end != -1:
        econ_prompt_block = content[econ_prompt_start:econ_prompt_end]
        
        data_block = econ_prompt_block[18:].strip()
        
        new_econ_prompt = f'''    prompt = f"""
    Your task is to synthesize the raw market news with quantitative ETF price action to update the global Economy Card.
    Follow the exact JSON schema provided in the system prompt.
    
    --- START OF DATA ---
    
    {data_block}
    
    --- END OF DATA ---
    Begin your JSON output now.'''
        
        content = content[:econ_prompt_start] + new_econ_prompt + content[econ_prompt_end:]

with open("modules/ai/ai_services.py", "w") as f:
    f.write(content)
