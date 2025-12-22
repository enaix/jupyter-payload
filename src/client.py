import asyncio
import websockets
import requests
import json
import uuid
import sys
import os
from aiohttp import web


class JupyterKernelClient:
    def __init__(self, jupyter_url: str, token: str, username: str = "client"):
        """
        Generic Jupyter Kernel WebSocket client for executing arbitrary code.
        
        Args:
            jupyter_url: e.g., 'https://jupyterhub.domain/user/username'
            token: JupyterHub API token
            username: Jupyter username
        """
        self.jupyter_url = jupyter_url.rstrip('/')
        self.token = token
        self.session_id = None
        self.kernel_id = None
        self.ws = None
        self.username = username
        
    def _headers(self):
        return {
            'Authorization': f'token {self.token}',
            'Content-Type': 'application/json'
        }
    
    def create_session(self, notebook_path: str = 'test.ipynb', kernel_name: str = 'python3'):
        """Create or get existing session. Takes the first avaliable session by default"""
        # First, try to get existing sessions
        resp = requests.get(
            f'{self.jupyter_url}/api/sessions',
            headers=self._headers(),
            verify=True  # Set to False if using self-signed certs
        )
        resp.raise_for_status()
        
        sessions = resp.json()
        if sessions:
            # Use first available session
            # TODO change this behavior
            self.session_id = sessions[0]['id']
            self.kernel_id = sessions[0]['kernel']['id']
            print(f"create_session() : using existing session: {self.session_id}")
            print(f"create_session() : kernel ID: {self.kernel_id}")
            return
        
        # Create new session
        print(f"create_session() : creating new session with notebook: {notebook_path}")
        resp = requests.post(
            f'{self.jupyter_url}/api/sessions',
            headers=self._headers(),
            json={
                'path': notebook_path,
                'type': 'notebook',
                'kernel': {'name': kernel_name}
            },
            verify=True
        )
        resp.raise_for_status()
        
        data = resp.json()
        self.session_id = data['id']
        self.kernel_id = data['kernel']['id']
        print(f"create_session() : created session: {self.session_id}")
        print(f"create_session() : kernel ID: {self.kernel_id}")


    def kill_session(self):
        """Kill the current kernel session"""
        if not self.session_id:
            print("kill_session() : no active session to kill")
            return

        print(f"kill_session() : killing session: {self.session_id}")
        resp = requests.delete(
            f'{self.jupyter_url}/api/sessions/{self.session_id}',
            headers=self._headers(),
            verify=True
        )
        resp.raise_for_status()
        print(f"kill_session() : session killed successfully")

        self.session_id = None
        self.kernel_id = None


    async def connect_websocket(self):
        """Connect to kernel WebSocket"""
        # Convert https to wss, http to ws
        ws_url = self.jupyter_url.replace('https://', 'wss://').replace('http://', 'ws://')
        ws_url = f"{ws_url}/api/kernels/{self.kernel_id}/channels"
        
        # Add token to URL for WebSocket auth
        ws_url = f"{ws_url}?token={self.token}"
        
        print(f"connect_websocket() : connecting to WebSocket: {ws_url.replace(self.token, '***')}")
        
        # Connect with SSL context if needed
        self.ws = await websockets.connect(
            ws_url,
            # Uncomment if using self-signed certificates:
            # ssl=ssl.create_default_context()
        )
        print(f"connect_websocket() : WebSocket connected to kernel")


    async def send_message(self, msg_id: str, msg_type: str, content: dict, parent_header: dict = {}, channel: str = 'shell'):
        """
        Sends the message to the kernel.

        Args:
            msg_id: Generated message_id (use cli.new_msg_id)
            msg_type: Message type (jupyter client docs)
            content: Message contents (jupyter client docs)
            parent_header: Parent message header of the related message. *_reply messages must include the corresponding *_request message header
            channel: Message bus channel ('shell'/'stdin'/... from jupyter client docs)

        Returns:
            Sent message header
        """
        message = {
            'header': {
                'msg_id': msg_id,
                'username': self.username,
                'session': self.session_id,
                'msg_type': msg_type,
                'version': '5.3'
            },
            'parent_header': parent_header,
            'metadata': {},
            'content': content,
            'buffers': [],
            'channel': channel
        }

        # Send execute request
        # TODO do we need a timeout?
        await self.ws.send(json.dumps(message))
        return message['header']


    def new_msg_id(self):
        return str(uuid.uuid4())


    async def execute_code_nowait(self, code: str, verbose: bool = True, stdin: bool = False):
        """
        Execute arbitrary code in the kernel and return its message id.

        Args:
            code: Python code to execute
            verbose: Print execution progress

        Returns:
            Message id and message header
        """
        msg_id = self.new_msg_id()

        if verbose:
            print(f"\n{'='*60}")
            print(f"Executing code (msg_id: {msg_id[:8]}...):")
            print(f"{'='*60}")
            print(code)
            print(f"{'='*60}\n")

        # Construct execute_request message
        # https://jupyter-client.readthedocs.io/en/stable/messaging.html#the-wire-protocol
        content = {
            'code': code,
            'silent': False,  # We may want to set it to True
            'store_history': True, # Same for this flag
            'user_expressions': {},
            'allow_stdin': stdin, # Run either in the non-interactive or interactive mode
            'stop_on_error': True
        }

        # Send execute request
        header = await self.send_message(msg_id, 'execute_request', content)
        #print("HEADER: ", header)
        return msg_id, header


    async def collect_execution_output(self, msg_id: str, timeout: int = 60, verbose: bool = True):
        """
        Wait and return the outputs of a code cell. This function works for sequential code execution

        Args:
            msg_id: Message id
            timeout: Maximum time to wait for execution (seconds)
            verbose: Print execution progress

        Returns:
            dict with 'status', 'outputs', 'error' keys
        """
        # Collect all outputs
        outputs = []
        error = None
        status = 'unknown'

        try:
            async with asyncio.timeout(timeout):
                received_output = False
                got_exec_reply = False
                while (not received_output) or (not got_exec_reply):
                    msg = await self.read_message(timeout, verbose)

                    if msg['msg_id'] != msg_id:
                        continue

                    if msg.get('output') is not None:
                        outputs.append(msg['output'])
                        received_output = True

                    if msg.get('error') is not None:
                        received_output = True
                        error = msg['error']

                    if msg.get('status') is not None:
                        got_exec_reply = True
                        status = msg['status']
                        # We don't need to handle timeout here


        except asyncio.TimeoutError:
            print(f"\nExecution timed out after {timeout}s")
            status = 'timeout'
    
        return {
            'status': status,
            'outputs': outputs,
            'error': error,
            'msg_id': msg_id
        }


    async def read_message(self, timeout: int = 60, verbose: bool = True):
        """
        Read one message from the kernel for all cells. Should be called from an event loop

        Args:
            timeout: Maximum time to wait for execution (seconds)
            verbose: Print execution progress

        Returns:
            dict with 'exec_reply' keys.
        """
        # Collect all outputs
        error = None
        status = None
        output = None
        msg_id = None
        msg_type = None
        msg = None

        try:
            async with asyncio.timeout(timeout):
                response = await self.ws.recv()
                msg = json.loads(response)

                msg_id = msg.get('parent_header', {}).get('msg_id')

                msg_type = msg.get('msg_type')

                if verbose:
                    print(f"<<< [{msg_type}]", end=' ')

                # Collect different types of output
                # Unhandled message types:
                # 'status': kernel status
                # ...
                if msg_type == 'stream':
                    output = {
                        'type': 'stream',
                        'name': msg['content']['name'],  # stdout or stderr
                        'text': msg['content']['text']
                    }
                    if verbose:
                        print(f"{output['name']}: {output['text'][:50]}...")

                elif msg_type == 'execute_result':
                    output = {
                        'type': 'execute_result',
                        'data': msg['content']['data'],
                        'execution_count': msg['content']['execution_count']
                    }
                    if verbose:
                        print(f"Result: {str(output['data'])[:50]}...")

                elif msg_type == 'display_data':
                    output = {
                        'type': 'display_data',
                        'data': msg['content']['data']
                    }
                    if verbose:
                        print(f"Display: {list(output['data'].keys())}")

                elif msg_type == 'error':
                    error = {
                        'ename': msg['content']['ename'],
                        'evalue': msg['content']['evalue'],
                        'traceback': msg['content']['traceback']
                    }
                    if verbose:
                        print(f"\n Error: {error['ename']}: {error['evalue']}")

                # Main message reply type
                elif msg_type == 'execute_reply':
                    status = msg['content']['status']

                    # if status is ok, then msg contains "payload" and "user_expressions" dicts
                    if verbose:
                        print("HEADER:", msg["header"])
                        print("    PARENT HEADER:", msg["parent_header"])
                        print(f"\nExecution {status}")

                    # Avoid timeout?
                    #if status != 'ok':
                    #   received_output = True

                else:
                    if verbose:
                        print(f"Ignoring f{msg_type} msg: {str(msg['content'])[:50]}...")

        except asyncio.TimeoutError:
            if verbose:
                print(f"\nExecution timed out after {timeout}s")
            status = 'timeout'

        return {
            'status': status, # Only for execute_reply
            'output': output, # Skip if none
            'error': error,
            'msg_id': msg_id,
            # Extra fields
            'msg_type': msg_type,
            '_msg': msg
        }


    def glue_output(self, outputs: list):
        """
        Glues all output (text) together
        """
        output_text = ""
        for output in outputs:
            if output['type'] == 'stream':
                output_text += output['text']
            elif output['type'] == 'execute_result':
                # Get plain text representation
                data = output['data']
                if 'text/plain' in data:
                    output_text += str(data['text/plain'])
                else:
                    output_text += str(data)
        return output_text


    async def execute_code(self, code: str, timeout: int = 60, verbose: bool = True):
        """
        Execute arbitrary code in the kernel and return all outputs.
        
        Args:
            code: Python code to execute
            timeout: Maximum time to wait for execution (seconds)
            verbose: Print execution progress
            
        Returns:
            dict with 'status', 'outputs', 'error' keys
        """
        msg_id, _ = await self.execute_code_nowait(code, verbose)
        result = await self.collect_execution_output(msg_id, timeout, verbose)
        return result


    async def execute_and_get_output(self, code: str, output_type: str = 'text', timeout: int = 60, verbose: bool = False):
        """
        Execute code and return simplified output.
        
        Args:
            code: Code to execute
            output_type: 'text' (stdout) or 'all'
            timeout: Execution timeout
            
        Returns:
            String output or dict of all outputs
        """
        result = await self.execute_code(code, timeout=timeout, verbose=verbose)
        
        if result['status'] != 'ok':
            if result['error']:
                raise Exception(f"{result['error']['ename']}: {result['error']['evalue']}")
            else:
                raise Exception(f"Execution failed with status: {result['status']}")
        
        if output_type == 'all':
            return result['outputs']
        
        # TODO change output_type to something more meaningful
        res = self.glue_output(result['outputs'])
        return res


    async def http_request(self, method: str, url: str, headers: dict = {}, body = None, timeout: int = 60, verbose: bool = True):
        """
        Send an HTTP request through the kernel to localhost.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: endpoint on Jupyter server
            headers: Dict of HTTP headers
            body: Request body (will be JSON encoded if dict)
            timeout: Request timeout
            verbose: Print verbose output
            
        Returns:
            dict with 'status_code', 'headers', 'body' keys
        """
        # Prepare body
        if isinstance(body, dict):
            body_str = json.dumps(body)
            if 'Content-Type' not in headers:
                headers['Content-Type'] = 'application/json'
        else:
            body_str = body or ''
        
        # Escape strings for Python code
        body_escaped = body_str.replace('\\', '\\\\').replace("\'", "\\'").replace("\"", "\\").replace('\n', '\\n')
        headers_json = json.dumps(headers)
        
        code = f'''
import requests
import json

try:
    url = f'{url}'
    
    response = requests.request(
        method='{method}',
        url=url,
        headers={headers_json},
        data=\'\'\'{body_escaped}\'\'\',
        timeout={timeout}
    )
    
    result = {{
        'status_code': response.status_code,
        'headers': dict(response.headers),
        'body': response.text,
        'ok': response.ok
    }}
    
    print(json.dumps(result))
    
except Exception as e:
    error_result = {{
        'status_code': 0,
        'headers': {{}},
        'body': '',
        'error': str(e),
        'ok': False
    }}
    print(json.dumps(error_result))
'''
        
        output = await self.execute_and_get_output(code, output_type='text', timeout=timeout, verbose=verbose)
        return json.loads(output.strip())


    async def close(self, kill=False):
        """Close WebSocket connection"""
        if self.ws:
            await self.ws.close()
            print("close() : WebSocket closed")
            if kill:
                self.kill_session()
