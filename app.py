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
showReset = False

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
    attachments = []
    if file is not None:
        attachments.append(
            {"file_id": file.id, "tools": [{"type": "code_interpreter"}, {"type": "file_search"}]}
        )
    client.beta.threads.messages.create(
        thread_id=thread.id, role="user", content=content, attachments=attachments
    )


def create_file_link(file_name, file_id):
    content = client.files.content(file_id)
    content_type = content.response.headers["content-type"]
    b64 = base64.b64encode(content.text.encode(content.encoding)).decode()
    link_tag = f'<a href="data:{content_type};base64,{b64}" download="{file_name}">Download Link</a>'
    return link_tag


def format_annotation(text):
    citations = []
    text_value = text.value
    for index, annotation in enumerate(text.annotations):
        text_value = text_value.replace(annotation.text, f" [{index}]")

        if file_citation := getattr(annotation, "file_citation", None):
            cited_file = client.files.retrieve(file_citation.file_id)
            citations.append(
                f"[{index}] {file_citation.quote} from {cited_file.filename}"
            )
        elif file_path := getattr(annotation, "file_path", None):
            link_tag = create_file_link(
                annotation.text.split("/")[-1],
                file_path.file_id,
            )
            text_value = re.sub(r"\[(.*?)\]\s*\(\s*(.*?)\s*\)", link_tag, text_value)
    text_value += "\n\n" + "\n".join(citations)
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
    </style>
""" 
    #Enable CSS
    st.markdown(css, unsafe_allow_html=True)

    #Now construct Web Page via HTML
    st.logo(image='DiversionsLogo.png')
    st.markdown('<h1 id="welcome">Welcome!</h1>', unsafe_allow_html=True)
    st.markdown('''<div id="instructionContainer"><b><p id="instructionText">I'm Johm.<br>I know the Diversions game library inside and out!<br>Ask me for a recommendation!</p></b></div>''',unsafe_allow_html=True)
    playerCountContainer = st.container(key="playerCountContainer")
    col1, col2, col3, col4, col5, col6, col7 = playerCountContainer.columns(7)
    with col1:
        if st.button("1 Player",key="playerOne",use_container_width=True):
            st.write("1 Player")
        
    with col2:
        if st.button("2 Players",key="playerTwo",use_container_width=True):
            st.write("2 Players")
        
    with col3:
        if st.button("3 Players",key="playerThree",use_container_width=True):
            st.write("3 Players")
        
    with col4:
        if st.button("4 Players",key="playerFour",use_container_width=True):
            st.write("4 Players")
        
    with col5:
        if st.button("5 Players",key="playerFive",use_container_width=True):
            st.write("5 Players")
        
    with col6:
        if st.button("6 Players",key="playerSix",use_container_width=True):
            st.write("6 Players")
        
    with col7:
        if st.button("7+ Players",key="playerSeven",use_container_width=True):
            st.write("7 Players")
    playerCountContainer.markdown('''<div id="underPlayerCountBlurb"><b><p id="underPlayerCountBlurbText">View Top Games by Player Count</p></b></div>''',unsafe_allow_html=True)

    alternateStartersContainer = st.container(key="alternateStartersContainer")
    altcol1, altcol2, altcol3 = alternateStartersContainer.columns(3)
    with altcol1:
        if st.button("Teach me about board games",key="Teach",use_container_width=True):
            st.write("Teach me about board games")

    with altcol2:
        if st.button("I'm not sure where to start",key="Unsure",use_container_width=True):
            st.write("I'm not sure where to start")

    with altcol3:
        if st.button("Shuffle and deal me",key="Shuffle",use_container_width=True):
            st.write("Shuffle and deal me")
    
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
