import sys
import os
import tiktoken
import json
import glob
import openai
import re
import shutil
import warnings

from termcolor import colored
from config import OPENAI_API_KEY
import requests
from tenacity import retry, wait_random_exponential, stop_after_attempt

# Override the default formatwarning
def custom_formatwarning(msg, category, filename, lineno, line=None):
    ''' DO NOT CALL - only used to define the custom format'''
    return f"{category.__name__}: {msg}\n"
# Set the warnings format to use the custom format
warnings.formatwarning = custom_formatwarning

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir) # Add the parent directory to sys.path

os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
#OPENAI_LLM = "gpt-3.5-turbo"  # OpenAI model name - comment one out
#OPENAI_LLM = "gpt-4"  # OpenAI model name - comment one out
OPENAI_LLM = "gpt-4-1106-preview"  # OpenAI model name - comment one out

#encoding = tiktoken.get_encoding("cl100k_base")

BLOCK_DELIMITER = '\n---\n'

### INITIAL HELPERS
# newly created and not used or tested
def pretty_print_function(messages, tools, print_prompts=False, print_input=True, verbose=False):  # DS, cat 1, unittests 2 - could be improved
    """
    Prints messages with role-specific colors and separates function details for clarity.

    :param messages: list of dictionaries containing message role and content
    :params tools: list of tools, each containing function details, passed to pretty_print_function_descriptions
    :param print_prompts: boolean of whether to print the system prompt and function parameter descriptions, defaults to False
    :param print_input: boolean of whether to print the user input, defaults to True
    :return: a list of the print strings as [print_str_prompts, print_str_input, print_str_responses]

    :category: 1
    :area: INITIAL HELPERS
    :usage: pretty_print_function(list_of_messages, tools)
    """
    role_to_color = {
        "system": "red",
        "function parameter descriptions": "yellow",
        "user": "green",
        "assistant": "grey",
        "function responses": "magenta",  # Color for function details
    }
    from general.fileops import verbose_print

    print_str_prompts = ""
    print_str_input = ""
    print_str_responses = ""

    verbose_print(verbose, f"messages:\n{messages}")
    verbose_print(verbose, f"tools:\n{tools}")

    for index, message in enumerate(messages):
        verbose_print(verbose, f"Message {index + 1} of {len(messages)} messages")
        if message["role"] == "system" and print_prompts:
            print_str_prompts = f"System Prompt: {message['content']}\n"
            print(colored(print_str_prompts, role_to_color[message["role"]]))
            if tools is not None:
                print_str_prompts += pretty_print_function_descriptions(tools, role_to_color["function parameter descriptions"])
        if message["role"] == "user":
            print_str_input = f"User Input:\n{message['content']}\n"
            print(colored(print_str_input, role_to_color[message["role"]]))
        elif message["role"] == "assistant":
            assistant_msg_str = str(message)

            # Find the index where function details start
            function_start_idx = assistant_msg_str.find("'function': ")

            # Split the string into two parts
            assistant_msg_part = assistant_msg_str[:function_start_idx]
            function_msg_part = assistant_msg_str[function_start_idx:]

            # Print the technical assistant info but generally we don't need this so comment out 3-16-24 RT
            #print(colored(f"assistant: {assistant_msg_part}", role_to_color["assistant"]))

            # Process and print the second part (function details) in magenta
            if function_start_idx != -1:
                # Parsing the function details from the string
                function_name_start_idx = function_msg_part.find("'name': '") + len("'name': '")
                function_name_end_idx = function_msg_part.find("'", function_name_start_idx)
                function_name = function_msg_part[function_name_start_idx:function_name_end_idx]

                arguments_str = function_msg_part.split("'arguments': '{", 1)[-1].rstrip("}'}}]")
                arguments_str = arguments_str.replace("\\n", "\n").replace("\\", "").replace('"', '')
                print_str_responses = "Function Parameters Responses:\n"
                for line in arguments_str.split(","):
                    # below is simply to add a space after the colon for reasons I do not understand!
                    key_value = line.split(':', 1)
                    if len(key_value) == 2:
                        key, value = key_value
                        print_str_responses += f"  {key.strip()}: {value.strip()}\n"
                    else:
                        print_str_responses += f"  {line}\n"
                print(colored(print_str_responses, role_to_color["function responses"]))

    return [print_str_prompts, print_str_input, print_str_responses]

def pretty_print_function_descriptions(tools, print_color):  # DS, cat 1, unittests 1
    """
    Print descriptions of functions and their properties from a list of tools.

    :param tools: a list of tools, each containing function details
    :return: a string of function names and descriptions, including properties

    :category: DO NOT FILL IN BUT LEAVE HERE
    :area: DO NOT FILL IN BUT LEAVE HERE
    :usage: print(pretty_print_function_descriptions(my_tools))
    """
    output_str = ""
    for tool in tools:
        if tool["type"] == "function":
            function_name = f"Function Name: {tool['function']['name']}"
            function_description = f"Function Description: {tool['function']['description']}"
            output_str += function_name + "\n" + function_description + "\n"
            print(colored(output_str, print_color))
            output_str += "Function Parameter Descriptions:\n"
            print(colored("Function Parameter Descriptions:", print_color))
            output_str += '\n'  # add newline so string matches terminal print
            
            # Extract and print each function's properties with descriptions, appending them to the output string.
            properties = tool['function']['parameters']['properties']
            for prop, details in properties.items():
                prop_description = f"  {prop}: {details['description'].strip()}"
                output_str += prop_description + "\n"
                print(colored(prop_description, print_color))
            
    return output_str

### SEPARATE BLOCKS FUNCTIONS
def get_line_numbers_with_match(file_path, match_str):  # DS, cat 1, unittests 3
    """
    Retrieve line numbers from a file where the line matches a given string exactly after stripping.

    :param file_path: path to the file to be searched
    :param match_str: string of text to match on each line
    :return: list of line numbers where the match_str is found

    :category: 1
    :area: SEPARATE BLOCKS FUNCTIONS
    :usage: print(get_line_numbers_with_match('test_blocks.md', '---'))
    """
    if not os.path.exists(file_path):
        raise ValueError(f"the file at {file_path} does not exist.")
    
    line_numbers = []
    with open(file_path, 'r') as file:
        for line_number, line in enumerate(file, start=1):
            if line.strip() == match_str:
                line_numbers.append(line_number)
    
    return line_numbers

def get_speaker_segments(file_path, skip_string='SKIPQA'):  # DS, cat 3a, unittests 2
    """
    Extract segments from a file that do not contain a specific skip string, or all segments if skip string is None.

    :param file_path: string of the path to the file to be processed
    :param skip_string: string of the substring used to identify segments to skip, or None to include all segments
    :return: list of segments without the skip string, or all segments if skip string is None

    :category: 3a non ffop file function
    :area: SEPARATE BLOCKS FUNCTIONS
    :usage: segments = get_speaker_segments('path/to/file.md', 'SKIPQA')
    """
    from general.fileops import get_heading_from_file
    transcript = get_heading_from_file(file_path, heading="### transcript")
    transcript = transcript.lstrip('### transcript').lstrip('\n*')
    
    segments = transcript.split("\n\n")
    if skip_string is not None:
        segments = [segment.strip() for segment in segments if skip_string not in segment]
    else:
        segments = [segment.strip() for segment in segments]
    
    return segments

def group_segments_select_speaker(segments, speaker):  # DS, cat 1, unittests 2
    """
    Groups consecutive segments not containing the specified speaker's name and selects segments where the speaker's name is found before the timestamp.
    Calls get_timestamp from fileops.py to determine if the first line in a segment is a speaker line.

    :param segments: list of text segments to be processed
    :param speaker: string of the speaker's name to select segments
    :return: list of text segments where the speaker's name is found before the timestamp

    :category: 1
    :area: SEPARATE BLOCKS FUNCTIONS
    :usage: print(filter_segments_select_speaker(list_of_segments, 'David Deutsch'))
    """
    from general.fileops import get_timestamp
    final_segments = []
    temp_segments = []
    for segment in segments:
        first_line = segment.split('\n', 1)[0]  # Extract the first line of the segment
        timestamp, index = get_timestamp(first_line)
        # Check if the speaker's name is in the portion of the first line before the timestamp index
        if timestamp is not None and speaker in first_line[:index]:
            temp_segments.append(segment)
            final_segments.append('\n\n'.join(temp_segments))
            temp_segments = []
        else:
            temp_segments.append(segment)
    
    # Check if the last temp_segments should be added
    if temp_segments:
        last_segment_first_line = temp_segments[-1].split('\n', 1)[0]
        timestamp, index = get_timestamp(last_segment_first_line)
        if timestamp is not None and speaker in last_segment_first_line[:index]:
            final_segments.append('\n\n'.join(temp_segments))
    
    return [segment for segment in final_segments if segment.strip()]

def group_segments_token_cap(segments, token_cap=2000):  # DS, cat 1, unittests 4
    """
    Groups consecutive segments without exceeding the token_cap, without splitting segments.
    Includes segments that exceed the token_cap as individual blocks.

    :param segments: list of text segments to be processed
    :param token_cap: integer of maximum number of tokens, using words = .75 tokens
    :return: list of grouped text segments without exceeding the token_cap, including oversized segments as individual blocks

    :category: 1
    :area: SEPARATE BLOCKS FUNCTIONS
    :usage: print(group_segments_token_cap(list_of_segments, token_cap=2000))
    """
    final_segments = []
    temp_segments = []
    current_token_count = 0
    word_cap = token_cap / 0.75

    for segment in segments:
        words_in_segment = len(segment.split())
        if words_in_segment > word_cap:
            # If there are any segments in temp_segments, add them to final_segments
            if temp_segments:
                final_segments.append('\n\n'.join(temp_segments))
                temp_segments = []
                current_token_count = 0
            # Add the oversized segment as an individual block
            final_segments.append(segment)
        else:
            if current_token_count + (words_in_segment * 0.75) > token_cap:
                # If adding the segment would exceed the token cap, finalize the current group
                final_segments.append('\n\n'.join(temp_segments))
                temp_segments = [segment]
                current_token_count = words_in_segment * 0.75
            else:
                # Otherwise, add the segment to the current group
                temp_segments.append(segment)
                current_token_count += words_in_segment * 0.75

    # Add any remaining segments in temp_segments to final_segments
    if temp_segments:
        final_segments.append('\n\n'.join(temp_segments))

    return [segment for segment in final_segments if segment.strip()]

def separate_blocks_select_speaker_ffop(file_path, speaker, skip_string='SKIPQA', suffix_new='_blocks'):  # DS, cat 2a, unittests 1
    """
    Add block delimiters to a file, with a block for every segment by the selected speaker and other segments combined above.

    :param file_path: path to the file to be processed
    :param speaker: the speaker whose sections will be delimited
    :param skip_string: string to identify speaker segments to skip
    :param suffix_new: suffix for the new file with block delimiters
    :return: file_path of new file with separator delimiters ("---") with suffix_new='_blocks' by default

    :category: 2b nested ffop - calls write_header_and_content_ffop without do_ffop
    :area: SEPARATE BLOCKS FUNCTIONS
    :usage: blocks_file_path = do_ffop(separate_blocks_select_speaker_ffop, cur_file_path, "David Deutsch", overwrite='no')
    """
    from general.fileops import read_header_and_content_from_file, write_header_and_content_ffop

    header, _ = read_header_and_content_from_file(file_path, delimiter="## content")
    segments = get_speaker_segments(file_path, skip_string)
    #print(f"\nDEBUG separate segments: {segments}")
    grouped_segments = group_segments_select_speaker(segments, speaker)
    # if final_segments:
    #     print(f"DEBUG: First element of final_segments: {repr(final_segments[0][:100])}")
    new_content = BLOCK_DELIMITER.join(grouped_segments)  # Using the global variable BLOCK_DELIMITER  
    return write_header_and_content_ffop(file_path, header, new_content, suffix_new)

def separate_blocks_every_speaker_ffop(file_path, skip_string=None, suffix_new='_blocks'):  # DS, cat 2a, unittests 1
    """
    Add block delimiters to a file with one block per speaker segment regardless of speaker.

    :param file_path: path to the file to be processed
    :param skip_string: string to identify speaker segments to skip
    :param suffix_new: suffix for the new file with block delimiters
    :return: file_path of new file with separator delimiters ("---") with suffix_new='_blocks' by default

    :category: 2b nested ffop - calls write_header_and_content_ffop without do_ffop
    :area: SEPARATE BLOCKS FUNCTIONS
    :usage: blocks_file_path = do_ffop(separate_blocks_every_speaker_ffop, cur_file_path, overwrite='no')
    """
    from general.fileops import read_header_and_content_from_file, write_header_and_content_ffop

    header, _ = read_header_and_content_from_file(file_path, delimiter="## content")
    segments = get_speaker_segments(file_path, skip_string)
    #print(f"\nDEBUG separate segments: {segments}")
    new_content = BLOCK_DELIMITER.join(segments)  # Using the global variable BLOCK_DELIMITER  
    return write_header_and_content_ffop(file_path, header, new_content, suffix_new)

def separate_blocks_token_cap_ffop(file_path, token_cap, skip_string='SKIPQA', suffix_new='_blocks'):  # DS, cat 2a, unittests
    """
    Add block delimiters to a file with one block per speaker segment regardless of speaker.

    :param file_path: path to the file to be processed
    :param token_cap: integer of maximum number of tokens, using words = .75 tokens
    :param skip_string: string to identify speaker segments to skip
    :param suffix_new: suffix for the new file with block delimiters
    :return: file_path of new file with separator delimiters ("---") with suffix_new='_blocks' by default

    :category: 2b nested ffop - calls write_header_and_content_ffop without do_ffop
    :area: SEPARATE BLOCKS FUNCTIONS
    :usage: blocks_file_path = do_ffop(separate_blocks_token_cap_ffop, cur_file_path, token_cap=2000, overwrite='no')
    """
    from general.fileops import read_header_and_content_from_file, write_header_and_content_ffop

    header, _ = read_header_and_content_from_file(file_path, delimiter="## content")
    segments = get_speaker_segments(file_path, skip_string)
    #print(f"\nDEBUG separate segments: {segments}")
    grouped_segments = group_segments_token_cap(segments, token_cap)
    new_content = BLOCK_DELIMITER.join(grouped_segments)  # Using the global variable BLOCK_DELIMITER  
    return write_header_and_content_ffop(file_path, header, new_content, suffix_new)

### LLM SIMPLE CALL FUNCTIONS
@retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
def openai_chat_completion_request(messages, tools=None, tool_choice=None, model=OPENAI_LLM):  # DS, cat 5, skip unittests
    """
    Send a chat completion request to the OpenAI API with the provided messages and optional tools and tool choice.

    :param messages: a list of message dictionaries to send in the chat completion request
    :param tools: optional list of tools to include in the request
    :param tool_choice: optional tool choice to include in the request
    :param model: the model to use for the chat completion request
    :return: the response object from the OpenAI API request

    :category: 5 api call
    :area: LLM CALL BASIC FUNCTIONS
    :usage: response = openai_chat_completion_request([{"role": "user", "content": "Hello, world!"}])
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + OPENAI_API_KEY,  # Use the imported API key instead of this way "openai.api_key,"
    }
    json_data = {"model": model, "messages": messages}
    if tools is not None:
        json_data.update({"tools": tools})
    if tool_choice is not None:
        json_data.update({"tool_choice": tool_choice})
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=json_data,
        )
        return response
    except Exception as e:
        print("Unable to generate ChatCompletion response")
        print(f"Exception: {e}")
        return e

def test_openai_chat(model=OPENAI_LLM):# DS, cat 5, skip unittests
    """
    Sends a predefined message to the OpenAI chat API and prints the response.

    :param model: string of the model name to be used for the chat completion request
    :return: None

    :category: 5 api call
    :area: LLM CALL BASIC FUNCTIONS
    :usage: test_openai_chat(model="text-davinci-003")
    """
    try:
        messages = [{"role": "user", "content": "Tell me a knock knock joke about science."}]
        response = openai_chat_completion_request(messages, model=model)
        if response:
            print("API chat response:", response.text)
    except Exception as e:
        print(f"Failed to access the OpenAI API: {e}")

def process_block_with_llm(block, prompt):  # DS, cat 1, unittests 3
    """
    Processes a single block of text with a given prompt using the OpenAI chat completion API.

    :param block: string of the text block to be processed.
    :param prompt: string of the prompt to use for the chat completion request.
    :return: string of the processed text block or None if no valid response is received.

    :category: 1
    :area: LLM CALL BASIC FUNCTIONS
    :usage: processed_text = process_block_with_llm(text_block, "Please summarize this text.")
    """
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": block}]
    chat_response = openai_chat_completion_request(messages)

    if chat_response.status_code == 200:
        response_json = chat_response.json()
        #print(f"DEBUG process_block_with_llm print full response_json:\n {response_json}")
        if 'choices' in response_json and len(response_json['choices']) > 0:
            return response_json['choices'][0]['message']['content']
        else:
            print("No 'choices' in response or 'choices' list is empty.")
            return None
    else:
        print(f"Request failed with status code {chat_response.status_code}: {chat_response.text}")
        return None

def process_file_blocks(blocks_file_path, prompt, operation_mode='replace', retain_delimiters=False, suffix_new=''):  # DS, cat 3a, unittests 3 
    """
    Processes blocks of text in a file using a specified prompt and operation mode, then writes the processed content back to the file.

    :param file_path: string of the path to the file containing text blocks
    :param prompt: string of the prompt to use for processing each text block
    :param operation_mode: string of the operation mode ('replace' or 'append') to handle the processed blocks
    :param retain_delimiters: boolean indicating whether to retain the original block delimiters in the new content
    :param suffix_new: string of the suffix to append to the file when saving the new content, a default value is required because it's after another kewword argument
    :return: the path to the file with the updated content

    :category: 3a non ffop file function
    :area: LLM CALL BASIC FUNCTIONS
    :usage: updated_file_path = process_file_blocks('path/to/file.md', 'Please summarize this block.', 'replace', False, '_summarized')
    """
    from general.fileops import read_header_and_content_from_file, write_header_and_content_ffop
    header, content = read_header_and_content_from_file(blocks_file_path)
    blocks = content.split(BLOCK_DELIMITER)

    processed_blocks = []
    print(f"BLOCKS TO PROCESS WITH SIMPLE LLM CALL: {len(blocks)}\n")  # Added print function
    print(colored(f"Simple LLM Call Prompt: {prompt}\n", "red"))  # Added print function

    for i, block in enumerate(blocks):
        print(f"\n\nBlock number: {i+1}")
        llm_response = process_block_with_llm(block, prompt)
        if llm_response:
            if operation_mode == 'replace':
                processed_blocks.append(llm_response)
            elif operation_mode == 'append':
                processed_blocks.append(block + "\n" + llm_response)
            print(colored("User Input:", "green"))
            print(colored(block, "green"))
            print(colored("LLM Response:", "blue"))
            print(colored(llm_response, "blue"))
        else:
            print("No response received for block.")
    print(colored(f"\nSingle LLM call prompt: {prompt}\n", "red"))
    
    new_content = '\n\n'.join(processed_blocks) if not retain_delimiters else BLOCK_DELIMITER.join(processed_blocks)
    return write_header_and_content_ffop(blocks_file_path, header, new_content, suffix_new)

def scall_replace_ffop(blocks_file_path, prompt, retain_delimiters=False, suffix_new='_scall-replace'):  # DS, cat 2a, unittests 2
    """
    Processes a file's text blocks and append after the original text the LLM-processed content based on a given prompt.

    :param blocks_file_path: string of the path to the file containing text blocks
    :param prompt: string of the prompt to use for processing each text block
    :param retain_delimiters: boolean indicating whether to retain the original block delimiters in the new content
    :param suffix_new: string of the suffix to append to the file when saving the new content
    :return: string of the path to the file with the updated content

    :category: 2a flat ffop
    :area: LLM CALL BASIC FUNCTIONS
    :usage: copyedit_file_path = do_ffop(scall_replace_ffop, blocks_file_path, PROMPT_COPYEDIT, retain_delimiters=True, suffix_new='_copyedit', overwrite="no-sub")
    """
    return process_file_blocks(blocks_file_path, prompt, 'replace', retain_delimiters, suffix_new)

def scall_append_ffop(blocks_file_path, prompt, retain_delimiters=False, suffix_new='_scall-add'):# DS, cat 2a, unittests 2
    """
    Processes a file's text blocks to replace them with LLM-processed content based on a given prompt.
    
    :param blocks_file_path: string of the path to the file containing text blocks
    :param prompt: string of the prompt to use for processing each text block
    :param retain_delimiters: boolean indicating whether to retain the original block delimiters in the new content
    :param suffix_new: string of the suffix to append to the file when saving the new content
    :return: string of the path to the file with the updated content

    :category: 2a flat ffop
    :area: LLM CALL BASIC FUNCTIONS
    :usage: keywords_file_path = do_ffop(scall_append_ffop, blocks_file_path, PROMPT_KEYWORDS, retain_delimiters=True, suffix_new='_keywords', overwrite="no-sub")
    """
    return process_file_blocks(blocks_file_path, prompt, 'append', retain_delimiters, suffix_new)

def mod_blocks_file_with_adjacent_words(blocks_file_path, adjacent_words):
    #fill in code to modify blocks by adding the adjacent_words number of words from the previous and next blocks
    from general.fileops import read_header_and_content_from_file, write_header_and_content_ffop, do_ffop
    header, content = read_header_and_content_from_file(blocks_file_path)
    blocks = content.split(BLOCK_DELIMITER)

    modified_blocks = []

    for i, block in enumerate(blocks):
        # Extract context from the preceding and following blocks
        prev_context = ' '.join(blocks[max(0, i-1)].split()[-adjacent_words:]) if i > 0 else ''
        next_context = ' '.join(blocks[min(len(blocks)-1, i+1)].split()[:adjacent_words]) if i < len(blocks)-1 else ''
        
        # Concatenate the context with the current block, ensuring two new lines between contexts and the block
        augmented_block = f"{prev_context}\n\nTARGET SEGMENT TO ANALYZE FOR TRANSITIONS\n{block}\n\n{next_context}".strip()
        modified_blocks.append(augmented_block)

    # Join modified blocks with or without delimiters based on your original structure
    new_content = BLOCK_DELIMITER.join(modified_blocks)
    
    # Overwrite the file with the modified content
    do_ffop(write_header_and_content_ffop, blocks_file_path, header, new_content, overwrite='yes')

def scall_replace_adjacent_words_ffop(blocks_file_path, prompt, adjacent_words, retain_delimiters=False, suffix_new='_scall-replace-adj'):
    mod_blocks_file_with_adjacent_words(blocks_file_path, adjacent_words)
    return process_file_blocks(blocks_file_path, prompt, 'replace', retain_delimiters, suffix_new)

### LLM FUNCTION CALLING FUNCTIONS
def openai_function_call(prompt_system, content, tools, verbose=False):  # DS, cat 5, unittests 1
    """
    Sends a prompt and content to the OpenAI LLM and returns the assistant's message.

    :param prompt_system: string of the system's prompt to initiate the conversation
    :param content: string of the user's content to process
    :param tools: list of dictionaries containing tool configurations
    :param verbose: boolean indicating whether to print detailed response text
    :return: string of the assistant's message from the LLM response

    :category: 5 api call
    :area: LLM CALL BASIC FUNCTIONS
    :usage: assistant_message = openai_function_call(system_prompt, user_content, tool_list, verbose=True)
    """
    messages = [{"role": "system", "content": prompt_system}, {"role": "user", "content": content}]
    print("OPENAI LLM = " + OPENAI_LLM)
    chat_response = openai_chat_completion_request(messages, tools=tools)

    print("Response Status Code:", chat_response.status_code)
    if verbose:
        print("Response Text:", chat_response.text)

    assistant_message = None  # Initialize to None or a sensible default
    try:
        assistant_message = chat_response.json()["choices"][0]["message"]
        messages.append({"role": "assistant", "content": assistant_message})
        pretty_print_function(messages, tools)
        # print(f"DEBUG openai_function_call print full messages:\n {messages}")
    except Exception as e:
        print("Error parsing response:", e)
    return(assistant_message)

def scall_replace_ffop(file_path, prompt, suffix_new = '_copyedit'):
    from general.fileops import read_header_and_content_from_file
    header, content = read_header_and_content_from_file(file_path)
    blocks = content.split(BLOCK_DELIMITER)

    # Loop through each block and make a chat completion request with a messages object
    edited_blocks = []
    for block in blocks:
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": block}]
        chat_response = openai_chat_completion_request(messages)
        if chat_response.status_code == 200:  # Check if the request was successful
            response_json = chat_response.json()
            # Assuming the response structure has a 'choices' list with a 'message' field
            if 'choices' in response_json and len(response_json['choices']) > 0:
                assistant_message = response_json['choices'][0]['message']['content']
                edited_blocks.append(assistant_message)
            else:
                print("No 'choices' in response or 'choices' list is empty.")
        else:
            print(f"Request failed with status code {chat_response.status_code}: {chat_response.text}")

    #TODO:rewrite suffix handling to use fileop functions
    edited_content = BLOCK_DELIMITER.join(edited_blocks)
    base_file_name, file_extension = os.path.splitext(file_path)
    base_file_name = base_file_name.rsplit('_', 1)[0]
    new_file_name = f"{base_file_name}{suffix_new}{file_extension}"
    with open(new_file_name, 'w') as llmcopyedit_file:
        llmcopyedit_file.write(header + edited_content) #TODO: Check if the headers are being duplicated

    return new_file_name


### LLM APPLICATION FUNCTIONS
PROMPT_TEST = """
summaize the text after the speaker line as 3 keywords that best captures what is said, returned as a single line with commas between the words. retain the speaker line exactly as it is and put the new key word line as the next line directly underneath"""

#### QA
SYSTEM_PROMPT_QA = """
You are an expert text analyzer that is trained in identifying questions or implied questions. You will be given dialogue and your role is to return a question that would make sense to ask, or was asked that is best answered by the final text block in the dialogue. You will use your tool to only return exact JSON in the format specified.
"""

def tools_qa(speaker):  # DS, cat 1, skip unittests
    """
    Generate a list of tools for question and answer extraction based on the speaker's response.

    :param speaker: string of the speaker's name whose responses are being analyzed
    :return: list of dictionaries containing tool configurations for QA extraction

    :category: 1
    :area: LLM APPLICATION FUNCTIONS - QA
    :usage: qa_text = openai_function_call(system_prompt_qa, block, tools_qa(speaker))
    """
    return[
{
    "type": "function",
    "function": {
        "name": "get_qa",
        "description": "Extract and modify a question into a generic form and provide the exact verbatim answer from a transcript",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description":f"""
                    The question should capture the essence of the original query posed by the interviewer in a simplified, generic form. It should focus on the core topic or idea, removing extraneous contextual details. The modified question should have semantic alignment with {speaker}'s answer. The question should be rephrased for a third-person audience, ensuring it is generalized and does not include direct references to {speaker}. DO NOT mention the name {speaker} in the question. The question should be written in such a way that it assumes that the answer provided is the best knowledge humanity has about this topic at present moment.  Some specific phrases to use include: 1) 'multiverse quantum theory' - rather than 'many-worlds interpretation of quantum'.
                    """ , 
                },
                "timestamp": {
                    "type": "string",
                    "description": f"""
                    The timestamp corresponding to the start of {speaker}'s response in the format H:MM:SS or MM:SS or M:SS (chose whichever is present in the input text). This timestamp is crucial for contextualizing the answer within the transcript and must be accurate to reflect the exact moment the response begins.
                    """, 
                },
            },
            "required": ["question","timestamp"], # BS- I made them all required so the files output consistently. It was prevously only question and answer.
        },
    },
    },
    ]

def fcall_qa_ffop(file_path, speaker, suffix_new="_qa"):  # DS, cat 2b, skip unittests because called below
    """ 
    Processes a transcript file already sectioned into blocks to generate new question and answer file.
    Answers are based on the speaker sements of the provided speaker.
    Uses OpenAI function calling.

    :param file_path: string of the path to the _blocks file to be processed.
    :param speaker: string of the speaker's name for the answers in QA.
    :param suffix_new: string of the suffix to be appended to the original filename for the new file. Defaults to "_qa".
    :return: string of the path to the newly created file with QA

    :category: 2b nested ffop
    :area: LLM APPLICATION WRAPPERS - QA
    :usage: qa_file_path = do_ffop(fcall_qa_ffop, blocks_file_path, speaker)
    """
    from general.fileops import do_ffop, read_header_and_content_from_file, add_timestamp_links_ffop
    from general.fileops import sub_suffix_in_str, set_last_updated

    print("***Running fcall_qa on file: " + file_path)
    
    header, content = read_header_and_content_from_file(file_path, delimiter="## content")
    header = set_last_updated(header, 'Created QA')

    blocks = content.split(BLOCK_DELIMITER)

    print(f"QA BLOCKS TO PROCESS: {len(blocks)}\n")
    print(colored(f"System Prompt: {SYSTEM_PROMPT_QA}\n", "red"))
    pretty_print_function_descriptions(tools_qa(speaker), "red")
    qa_file_path = sub_suffix_in_str(file_path, suffix_new)
    
    with open(qa_file_path, "w") as qa_file:
        # Write the header at the top of the QA file
        qa_file.write(header)
        for i, block in enumerate(blocks):
            print(f"\n\nQA BLOCK NUMBER: {i+1}")
            qa_text = openai_function_call(SYSTEM_PROMPT_QA, block, tools_qa(speaker)) # Finish function to return textual output 
            
            # Extract the 'arguments' field from the function call
            arguments_json = qa_text['tool_calls'][0]['function']['arguments']

            # Attempt to parse the JSON
            try:
                arguments = json.loads(arguments_json)
                question = arguments['question']
                timestamp = arguments['timestamp']
                answer = block[block.rfind(')') + 1:].strip()

            except json.decoder.JSONDecodeError as e:
                print("JSONDecodeError:", e)
            # Extract the question and answer

            qa_file.write(f"QUESTION: {question}\nTIMESTAMP: {timestamp}\nANSWER: {answer}\nEDITS: \nTOPICS: \nSTARS: \n\n")

    do_ffop(add_timestamp_links_ffop, qa_file_path, overwrite='yes')
    print("QA written to " + qa_file_path)
    print(colored(f"System Prompt: {SYSTEM_PROMPT_QA}\n", "red"))
    pretty_print_function_descriptions(tools_qa(speaker), "red")
    return qa_file_path
    
def create_qa_file(file_path, speaker):  # DS, cat 4, unittests 1
    """
    Processes a _prepqa file to generate QA question and answer blocks using OpenAI LLM function calling.

    :param file_path: string of the path to the _prepqa transcript file to be processed.
    :param speaker: string of the speaker's name to be used in processing.
    :return: None.

    :category: 4 wrapper
    :area: LLM APPLICATION WRAPPERS - QA
    :usage: do_ffop_on_folder(create_qa_file, 'data/f_c3_run_now', speaker='David Deutsch', suffix_include='_prepqa')
    """
    from general.fileops import do_ffop, delete_file
    
    blocks_file_path = do_ffop(separate_blocks_select_speaker_ffop, file_path, speaker, overwrite='no')
    qa_file_path = do_ffop(fcall_qa_ffop, blocks_file_path, speaker)
    delete_file(blocks_file_path)
    return qa_file_path


#### COPYEDIT
PROMPT_COPYEDIT = """
copyedit the text WIP"""

def create_copyedit_file(file_path, separate_blocks_function, prompt, *args, **kwargs):
    # uses an argument to pass in the seperator function, in case you want different types of blocks
    from general.fileops import do_ffop, delete_file
    blocks_file_path = do_ffop(separate_blocks_function, file_path, *args, overwrite='no', **kwargs)
    copyedit_file_path = do_ffop(scall_append_ffop, blocks_file_path, prompt, retain_delimiters=True, suffix_new='_copyedit', overwrite="no-sub")
    #delete_file(blocks_file_path)
    return copyedit_file_path

#### SPEAKER SEGMENT TRANSITIONS
PROMPT_TRANSITIONS = """
    Your task is to analyze transcripts for speaker transition errors. 
    You will do this on a single speaker segment where the speaker segments are identified by a speaker name followed by a time stamp with a link. And then on the next line, the segment text, which is the dialogue of what that speaker
    The intended target segment is identified as the segment that follows the following text "TARGET SEGMENT TO ANALYZE FOR TRANSITIONS".
    The input text I'm providing contains the ending speaker text from the speaker segment above, and it also contains below the target segment the beginning text from below. And those adjacent text are provided for context so you can look to see if there are words from the previous segment that should be in the target segment, and likewise if there are words from the next segment that should, that start the next segment that should be at the end of the target segment.    
    For your analysis, ignore any text on the speaker line itself, which is the line that contains the speaker name and the timestamp. There could be additional words after that for other processing. Just ignore those, such as 'SKIPQA'
    
    To do the analysis To look for possible transition errors in the target segment, what you should do is look at the ending words of the text above, which is from the previous segment, and see if they both, see if that text both looks out of place at the end of that text, and then insert that text at the beginning of the speaker segment text for the target segment and see if that fits better as a speaker dialogue. And you can also analyze that target speaker segment text to see if it looks out of place without the added text.

    If your analysis concludes that there are no transition errors in the text for the target segment, then make your response only the text "No suspected transition errors."
    If your analysis concludes that there are transition errors, then state what those are with quoted text, but do not reproduce the entire text for the target segment. I will make the modifications manually.
    """
PROMPT_TRANSITIONS_2 = """
    Your task is to analyze transcripts for speaker transition errors. You will be given entiere speaker segments and you will return suggestions if needed. Follow these guidelines:
1. **Speaker Transitions (ST) - Identifying Missing Speaker Transitions**:
   - ALWAYS Flag and suggest changes when there is a possible interjection from another speaker such as these listed:
        - 'Yes, I agree.'
        - 'Okay.'
        - 'Right.'
    - Be creative and think deeply about any sentence that could be from a different speaker and flag it with curly braces if there is doubt that the entire block is from a single speaker.
    - If there is any text at all that could be interpreted as coming from a seperate speaker, than Flag it for review.
    - Heavily favor tagging possible errors in the middle of a speaker block, rather than at the beggining or end. Do not flag anything at the beginning or the end of a block. assume that whatever is there is correct.
3. **Evaluating Overlapping Talk**:
   a) If overlapping talk is short and doesn't affect meaning, suggest integrating it into the next speaker segment.
   b) If moving the overlap confuses the start of the next segment and the overlapping statement is short and insignificant, suggest deletion.
   c) If the overlapping statement is significant and moving it confuses the start of the next segment, suggest adding a new speaker segment.

Your response if a change is considered to be needed should be of the entire text given to you, but with the problematic section enclosed in curly braces, followed by a few word description of the problem with '**' at either end. THE CURLY BRACES ARE AN IMPORTANT FLAG, USE THEM.
If no changes at all are to be needed, please only respond with 'N/A'. Only use 'N/A' when there are no errors in the entire block. The parts that are without error before or after an error should still be returned.
    """
def create_transitions_file(file_path, separate_blocks_function, prompt, *args, **kwargs):
    # uses an argument to pass in the seperator function, in case you want different types of blocks
    from general.fileops import do_ffop, delete_file
    adjacent_words = 10

    blocks_file_path = do_ffop(separate_blocks_function, file_path, *args, overwrite='no', **kwargs)
    transitions_file_path = do_ffop(scall_replace_adjacent_words_ffop, blocks_file_path, prompt, adjacent_words, retain_delimiters=True, suffix_new='_transitions', overwrite="no-sub")
    #delete_file(blocks_file_path)
    return transitions_file_path

#### QUOTATIONS
PROMPT_QUOTATIONS = """
You are an expert at transcript processing, you are to evaluate the provided text according to specific quotation guidelines. Your task is to ensure that all instances of direct speech, internal monologue, specific terms, and imitations are correctly enclosed in single quotation marks. Additionally, you must identify and correct instances where quotations are missing or misused. Follow these guidelines:

1. Use single quotes ' ' for direct speech in anothers voice. Include a comma before the quote if it's preceded by a speech attribution (e.g., he said, she asked). Example correction: John said hello → John said, 'hello'.

2. Use single quotes for internal monologue presented as direct speech.

3. For specific terms, jargon, or phrases, use quotes, but do not add commas before the quotes. Place punctuation inside the single quotes.

4. Contextually decide if quotes are needed for ambiguous sentences.

In addition to those rules for quotations, please note the following things to keep in mind.

*  Do not add quotes for indirect speech. ie: they expressed their appreciation for them

*  For nested dialogue, use single quotes for the primary speech and double quotes for the nested part. 

*  Keep punctuation inside the single quotes for full sentences. For fragments, place punctuation outside.

*  For interrupted dialogue, continue the sentence within the same quotes after the tag or action.

*  Use single quotes for special cases like sarcasm or mimicry.

* Pay special attention to specific and related patterns that my preceed a quotation. These patterns include but are not limited to:
"they might say, "
"say well, "
"oh,"
"might ask"

Your primary tasks are to:
- Identify and fix instances where quotations are incorrectly applied.
- Locate and edit parts of the text where quotations are necessary but missing, according to these guidelines.

Please evaluate the text provided and make necessary corrections or suggest where quotations should be added or amended.
If an existing quotation is found your response should be the quote itself in curly braces, followed by a few word description of the problem with '**' at either end. if there is no problem with the existing quotation, just say 'CORRECT' for that description. If there is text that is not enclosed in quotes that shouldnt be, then DONT RETURN ANYTHING FOR IT. IGNORE IT. THE CURLY BRACES ARE AN IMPORTANT FLAG, USE THEM.
If there is a section that, according to the rules should have a quote, then return the section that should be quoted, with a few extra words from the text on either side. the quotes should be applied and flagged by curly braces and a description that uses the number of the rule that is being referenced to make the call.
If no changes at all are to be needed, please only respond with 'N/A'. Only use 'N/A' when there are no errors or quotes in the entire block."""

# TODO add line to remove delimiter
def create_copyedit_file(file_path, seperate_blocks_function, prompt, *args):
    # uses an argument to pass in the seperator function, in case you want different types of blocks
    # TODO: implement a way to pass a new suffix_new into the ffop
    from general.fileops import do_ffop, delete_file
    blocks_file_path = do_ffop(seperate_blocks_function, file_path, *args, overwrite='no')
    copyedit_file_path = do_ffop(scall_replace_ffop, blocks_file_path, prompt )
    delete_file(blocks_file_path)
    return copyedit_file_path


### TO RECONSIDER
def phased_llm_copyedit(path, prompts_dict):
    previous_suffix = None
    for suffix, prompt in prompts_dict.items():
        if os.path.isdir(path):
            print(f"Processing directory for LLM copy editing: {path}")
            if previous_suffix:
                input_folder = os.path.join(path, previous_suffix)
                if os.path.exists(input_folder):
                    md_folder_llm_copyedit(input_folder, prompt, suffix, output_folder=path)
                else:
                    print(f"Expected subfolder '{previous_suffix}' not found in directory: {path}")
            else:
                md_folder_llm_copyedit(path, prompt, suffix)
        elif os.path.isfile(path):
            if previous_suffix:
                # Split the file path into the base name and extension
                base_file_name, file_extension = os.path.splitext(path)

                # Remove any existing suffix after the last underscore (if any)
                base_file_name = '_'.join(base_file_name.split('_')[:-1])

                # Construct the previous file name by appending the previous suffix and file extension
                previous_file_name = f"{base_file_name}{previous_suffix}{file_extension}"

                # Check if the file with the previous suffix exists
                if os.path.isfile(previous_file_name):
                    print(f"Processing file for LLM copy editing: {previous_file_name}")
                    llm_copyedit(previous_file_name, prompt, suffix)
                else:
                    print(f"Previous version of file '{previous_file_name}' not found. Skipping.")
            else:
                print(f"Processing file for LLM copy editing: {path}")
                llm_copyedit(path, prompt, suffix)
        else:
            print(f"Path does not exist: {path}")
        previous_suffix = suffix




### TO BE DELETED
# deprecated
def llm_copyedit_console(file_path, prompt):
    # Function to get content blocks which returns a dictionary
    # where keys are the first line of each block and values are the second line
    # Get the dictionary of content blocks using the function
    try:
        with open(file_path, 'r') as file:
            content = file.read()
        blocks = content.split('\n---\n')
        content_blocks = {block.split('\n')[0]: block.split('\n')[1] for block in blocks if len(block.split('\n')) > 1 and not block.strip().startswith('###')}
        # print(content_blocks)
    except Exception as e:
        print(f"Error: {e}")
        return None
    # Loop through each block and make a chat completion request with a messages object
    for key, value in content_blocks.items():
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": value}]
        chat_response = openai_chat_completion_request(messages)
        if chat_response.status_code == 200:  # Check if the request was successful
            response_json = chat_response.json()
            # Assuming the response structure has a 'choices' list with a 'message' field
            if 'choices' in response_json and len(response_json['choices']) > 0:
                assistant_message = response_json['choices'][0]['message']['content']
                # print(assistant_message)
                if '{' in assistant_message and '}' in assistant_message:
                    print(key)  # Print the key (first line of the block)
                    print(assistant_message)  # Print the assistant message
                    print("\n\n")
                    pass
            else:
                print("No 'choices' in response or 'choices' list is empty.")
        else:
            print(f"Request failed with status code {chat_response.status_code}: {chat_response.text}")
# deprecated
def llm_copyedit(file_path, prompt, suffix):
        # Read the file and store text
    with open(file_path, 'r') as file:
        text = file.read()
    # Find the H2 markdown header that says 'content'
    headers = text.split('\n## content\n')
    if len(headers) < 2:
        raise ValueError("The file does not contain the H2 header 'content'.")
    # Read everything under the H2 header 'content'
    content_under_content_header = headers[1]
    # Split the content into a list using '\n---\n' as a delimiter
    blocks = content_under_content_header.split('\n---\n')
    # Loop through each block and make a chat completion request with a messages object
    edited_blocks = []
    for block in blocks:
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": block}]
        chat_response = openai_chat_completion_request(messages)
        if chat_response.status_code == 200:  # Check if the request was successful
            response_json = chat_response.json()
            # Assuming the response structure has a 'choices' list with a 'message' field
            if 'choices' in response_json and len(response_json['choices']) > 0:
                assistant_message = response_json['choices'][0]['message']['content']
                edited_blocks.append(assistant_message)
            else:
                print("No 'choices' in response or 'choices' list is empty.")
        else:
            print(f"Request failed with status code {chat_response.status_code}: {chat_response.text}")
    # Combine the edited blocks with the delimiter
    edited_content = '\n---\n'.join(edited_blocks)
    # Split the file path into the base name and extension
    base_file_name, file_extension = os.path.splitext(file_path)
    # Remove the existing suffix after the last underscore (if any)
    base_file_name = base_file_name.rsplit('_', 1)[0]
    # Construct the new file name with the new suffix and the original file extension
    new_file_name = f"{base_file_name}{suffix}{file_extension}"

    # Write the new content to a file with the provided suffix
    with open(new_file_name, 'w') as llmcopyedit_file:
        # Write the first header and the edited content under the second header
        llmcopyedit_file.write(headers[0] + '\n## content\n' + edited_content)  # Ensure the correct header is used
#TODO: change name of input_folder, etc
def md_folder_llm_copyedit(input_folder, prompt, suffix, output_folder=None):
    # Get a list of all markdown files in the directory
    md_files = glob.glob(f"{input_folder}/*.md")
    # Set the output folder to input folder if not specified
    if output_folder is None:
        output_folder = input_folder
    # Create a subfolder for the edited files
    edited_folder_path = os.path.join(output_folder, suffix)
    if not os.path.exists(edited_folder_path):
        os.makedirs(edited_folder_path)

    for md_file in md_files:
        print(f"Processing file for LLM copy editing: {md_file}")
        try:
            llm_copyedit(md_file, prompt, suffix)
            # Move the edited file to the subfolder
            base_name = os.path.basename(md_file)
            # Remove the existing suffix after the last underscore (if any)
            base_file_name = '_'.join(base_name.split('_')[:-1])
            # Construct the new file name with the new suffix and the original file extension
            new_file_name = f"{base_file_name}{suffix}.md"
            # Include the full path for the source file
            source_file_path = os.path.join(input_folder, new_file_name)  # Ensure this points to where llm_copyedit saves the file
            # Move the file to the edited folder path
            shutil.move(source_file_path, os.path.join(edited_folder_path, new_file_name))
        except ValueError as e:
            print(f"Skipping file {md_file}: {e}")
