import os
import base64
import re
import json

import streamlit as st
from streamlit_extras.stylable_container import stylable_container
import openai
from openai import AssistantEventHandler
from tools import TOOL_MAP
from typing_extensions import override
from dotenv import load_dotenv

load_dotenv()


def str_to_bool(str_input):
    if not isinstance(str_input, str):
        return False
    return str_input.lower() == "true"


# Load environment variables
openai_api_key = os.environ.get("OPENAI_API_KEY")
instructions = os.environ.get("RUN_INSTRUCTIONS", "")
enabled_file_upload_message = False

client = openai.OpenAI(api_key=openai_api_key)

st.set_page_config(layout="wide")

class EventHandler(AssistantEventHandler):
    @override
    def on_event(self, event):
        pass

    @override
    def on_text_created(self, text):
        st.session_state.current_message = ""
        with st.chat_message("Assistant"):
            st.session_state.current_markdown = st.empty()

    @override
    def on_text_delta(self, delta, snapshot):
        if snapshot.value:
            text_value = re.sub(
                r"\[(.*?)\]\s*\(\s*(.*?)\s*\)", "Download Link", snapshot.value
            )
            st.session_state.current_message = text_value
            st.session_state.current_markdown.markdown(
                st.session_state.current_message, True
            )

    @override
    def on_text_done(self, text):
        format_text = format_annotation(text)
        #format_text = text.value
        st.session_state.current_markdown.markdown(format_text, True)
        st.session_state.chat_log.append({"name": "assistant", "msg": format_text})

    @override
    def on_tool_call_created(self, tool_call):
        if tool_call.type == "code_interpreter":
            st.session_state.current_tool_input = ""
            with st.chat_message("Assistant"):
                st.session_state.current_tool_input_markdown = st.empty()

    @override
    def on_tool_call_delta(self, delta, snapshot):
        if 'current_tool_input_markdown' not in st.session_state:
            with st.chat_message("Assistant"):
                st.session_state.current_tool_input_markdown = st.empty()

        if delta.type == "code_interpreter":
            if delta.code_interpreter.input:
                st.session_state.current_tool_input += delta.code_interpreter.input
                input_code = f"### code interpreter\ninput:\n```python\n{st.session_state.current_tool_input}\n```"
                st.session_state.current_tool_input_markdown.markdown(input_code, True)

            if delta.code_interpreter.outputs:
                for output in delta.code_interpreter.outputs:
                    if output.type == "logs":
                        pass

    @override
    def on_tool_call_done(self, tool_call):
        st.session_state.tool_calls.append(tool_call)
        if tool_call.type == "code_interpreter":
            if tool_call.id in [x.id for x in st.session_state.tool_calls]:
                return
            input_code = f"### code interpreter\ninput:\n```python\n{tool_call.code_interpreter.input}\n```"
            st.session_state.current_tool_input_markdown.markdown(input_code, True)
            st.session_state.chat_log.append({"name": "assistant", "msg": input_code})
            st.session_state.current_tool_input_markdown = None
            for output in tool_call.code_interpreter.outputs:
                if output.type == "logs":
                    output = f"### code interpreter\noutput:\n```\n{output.logs}\n```"
                    with st.chat_message("Assistant"):
                        st.markdown(output, True)
                        st.session_state.chat_log.append(
                            {"name": "assistant", "msg": output}
                        )
        elif (
            tool_call.type == "function"
            and self.current_run.status == "requires_action"
        ):
            with st.chat_message("Assistant"):
                msg = f"### Function Calling: {tool_call.function.name}"
                st.markdown(msg, True)
                st.session_state.chat_log.append({"name": "assistant", "msg": msg})
            tool_calls = self.current_run.required_action.submit_tool_outputs.tool_calls
            tool_outputs = []
            for submit_tool_call in tool_calls:
                tool_function_name = submit_tool_call.function.name
                tool_function_arguments = json.loads(
                    submit_tool_call.function.arguments
                )
                tool_function_output = TOOL_MAP[tool_function_name](
                    **tool_function_arguments
                )
                tool_outputs.append(
                    {
                        "tool_call_id": submit_tool_call.id,
                        "output": tool_function_output,
                    }
                )

            with client.beta.threads.runs.submit_tool_outputs_stream(
                thread_id=st.session_state.thread.id,
                run_id=self.current_run.id,
                tool_outputs=tool_outputs,
                event_handler=EventHandler(),
            ) as stream:
                stream.until_done()


def create_thread(content, file):
    return client.beta.threads.create()


def create_message(thread, content, file):
    client.beta.threads.messages.create(
        thread_id=thread.id, role="user", content=content, attachments=[]
    )


def create_file_link(file_name, file_id):
    content = client.files.content(file_id)
    content_type = content.response.headers["content-type"]
    b64 = base64.b64encode(content.text.encode(content.encoding)).decode()
    link_tag = f'<a href="data:{content_type};base64,{b64}" download="{file_name}">Download Link</a>'
    return link_tag


def format_annotation(text):
    text_value = text.value
    text_value = re.sub("【(.*?)】", "", text_value)
    return text_value


def run_stream(user_input, file, selected_assistant_id):
    if "thread" not in st.session_state:
        st.session_state.thread = create_thread(user_input, file)
    create_message(st.session_state.thread, user_input, file)
    with client.beta.threads.runs.stream(
        thread_id=st.session_state.thread.id,
        assistant_id=selected_assistant_id,
        event_handler=EventHandler(),
    ) as stream:
        stream.until_done()


def render_chat():
    for chat in st.session_state.chat_log:
        with st.chat_message(chat["name"]):
            st.markdown(chat["msg"], True)


if "tool_call" not in st.session_state:
    st.session_state.tool_calls = []

if "chat_log" not in st.session_state:
    st.session_state.chat_log = []

if "in_progress" not in st.session_state:
    st.session_state.in_progress = False


def disable_form():
    st.session_state.in_progress = True


def reset_chat():
    st.session_state.chat_log = []
    st.session_state.in_progress = False


def load_chat_screen(assistant_id, assistant_title):
    #First set Pertinent CSS
    css = """
    <style>
        #welcome {
            color: #2A4294;
            font-size: 9vh;
            text-align: center;
        }

        img[data-testid="stLogo"] {
            height: 9vh;
        }

        #instructionContainer {
            color: #141F2B;
            text-align: center;
        }

        #instructionText {
            font-size: 2vh;
        }

        .st-key-playerCountContainer {
            align-text: center;
            justify-content: center;
            background: #E3E8E9;
            border-radius: 15px;
            padding: 10px;
        }

        #underPlayerCountBlurb {
            color: #141F2B;
            text-align: center;
        }

        #underPlayerCountBlurbText {
            font-size: 1.5vh;
        }

        #MainMenu {
            display: none;
        }
    </style>
""" 
    #Enable CSS
    st.markdown(css, unsafe_allow_html=True)

    #Now construct Web Page via HTML
    st.logo(image='DiversionsLogo.png')
    with stylable_container("resetButton",
                                css_styles="""
                                    button {
                                        position: fixed;
                                        right: 10px;
                                        z-index: 99;
                                        background-color: #EC8824;
                                        color: #141F2B;
                                        border: 2px solid #141F2B;
                                }""",):
            resetButton = st.button("Reset", key="resetButton")
    st.markdown('<h1 id="welcome">Welcome!</h1>', unsafe_allow_html=True)
    st.markdown('''<div id="instructionContainer"><b><p id="instructionText">I'm Johm.<br>I know the Diversions game library inside and out!<br>Ask me for a recommendation!</p></b></div>''',unsafe_allow_html=True)
    playerCountContainer = st.container(key="playerCountContainer")
    col1, col2, col3, col4, col5, col6, col7 = playerCountContainer.columns(7)
    with col1:
        with stylable_container("playerOne",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            playerOne = st.button("1 Player", key="playerOne",use_container_width=True)
        
    with col2:
        with stylable_container("playerTwo",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            playerTwo = st.button("2 Players", key="playerTwo",use_container_width=True)
        
    with col3:
        with stylable_container("playerThree",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            playerThree = st.button("3 Players", key="playerThree",use_container_width=True)
        
    with col4:
        with stylable_container("playerFour",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            playerFour = st.button("4 Players", key="playerFour",use_container_width=True)
        
    with col5:
        with stylable_container("playerFive",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            playerFive = st.button("5 Players", key="playerFive",use_container_width=True)
        
    with col6:
        with stylable_container("playerSix",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            playerSix = st.button("6 Players", key="playerSix",use_container_width=True)
        
    with col7:
        with stylable_container("playerSeven",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            playerSeven = st.button("7 Players", key="playerSeven",use_container_width=True)
                                    
    playerCountContainer.markdown('''<div id="underPlayerCountBlurb"><b><p id="underPlayerCountBlurbText">View Top Games by Player Count</p></b></div>''',unsafe_allow_html=True)

    alternateStartersContainer = st.container(key="alternateStartersContainer")
    altcol1, altcol2, altcol3 = alternateStartersContainer.columns(3)
    with altcol1:
        with stylable_container("Teach",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            Teach = st.button("Teach me about board games", key="Teach",use_container_width=True)

    with altcol2:
        with stylable_container("Unsure",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            Unsure = st.button("I'm not sure where to start", key="Unsure",use_container_width=True)

    with altcol3:
        with stylable_container("Shuffle",
                                css_styles="""
                                    button {
                                    background-color: #BBE4F1;
                                    color: #141F2B;
                                    border: 2px solid #141F2B;
                                }""",):
            Shuffle = st.button("Shuffle and deal me", key="Shuffle",use_container_width=True)

    if resetButton:
        st.session_state.chat_log = []
        del st.session_state['thread']
        render_chat()
    buttonAutoMessage = None
    if playerOne:
        buttonAutoMessage = "Could you recommend me some games that I can play with only 1 player; especially if they're best at 1?"
    elif playerTwo:
        buttonAutoMessage = "Could you recommend me some games that I can play with only 2 players; especially if they're best at 2?"
    elif playerThree:
        buttonAutoMessage = "Could you recommend me some games that I can play with 3 players; especially if they're best at 3?"
    elif playerFour:
        buttonAutoMessage = "Could you recommend me some games that I can play with 4 players; especially if they're best at 4?"
    elif playerFive:
        buttonAutoMessage = "Could you recommend me some games that I can play with 5 players; especially if they're best at 5?"
    elif playerSix:
        buttonAutoMessage = "Could you recommend me some games that I can play with 6 players; especially if they're best at 6?"
    elif playerSeven:
        buttonAutoMessage = "Could you recommend me some games that I can play with 7 or more players; especially if they're best at 7 or more?"
    elif Teach:
        buttonAutoMessage = "I'm not super familiar with board game terminology, so I'm not sure how to ask you for recommendations. Could you tell me a bit about a few types of board games?"
    elif Unsure:
        buttonAutoMessage = "I'm a bit unsure how to start because I'm a bit new to board games. Could you help me figure out how where to start?"
    elif Shuffle:
        buttonAutoMessage = "Surprise me! Give me a few completely random games from the collection to choose from!"
    if buttonAutoMessage:
        #st.session_state.chat_log.append({"name": "user", "msg": buttonAutoMessage})
        run_stream(buttonAutoMessage, None, assistant_id)
        st.session_state.in_progress = False
        st.session_state.tool_call = None
        st.rerun()
    user_msg = st.chat_input(
        "Message", on_submit=disable_form, disabled=st.session_state.in_progress
    )
    if user_msg:
        render_chat()
        with st.chat_message("user"):
            st.markdown(user_msg, True)
        st.session_state.chat_log.append({"name": "user", "msg": user_msg})

        run_stream(user_msg, None, assistant_id)
        st.session_state.in_progress = False
        st.session_state.tool_call = None
        st.rerun()

    render_chat()
        
def main():
    single_agent_id = os.environ.get("ASSISTANT_ID", None)
    single_agent_title = os.environ.get("ASSISTANT_TITLE", "Assistants API UI")

    load_chat_screen(single_agent_id, single_agent_title)
    

if __name__ == "__main__":
    main()
