import asyncio
import websockets
import requests
import json
import uuid
import sys
import os


class JupyterKernelClient:
    def __init__(self, jupyter_url, token):
        """
        Generic Jupyter Kernel WebSocket client for executing arbitrary code.
        
        Args:
            jupyter_url: e.g., 'https://jupyterhub.domain/user/username'
            token: JupyterHub API token
        """
        self.jupyter_url = jupyter_url.rstrip('/')
        self.token = token
        self.session_id = None
        self.kernel_id = None
        self.ws = None
        
    def _headers(self):
        return {
            'Authorization': f'token {self.token}',
            'Content-Type': 'application/json'
        }
    
    def create_session(self, notebook_path='test.ipynb', kernel_name='python3'):
        """Create or get existing session"""
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
    
    async def execute_code(self, code, timeout=60, verbose=True):
        """
        Execute arbitrary code in the kernel and return all outputs.
        
        Args:
            code: Python code to execute
            timeout: Maximum time to wait for execution (seconds)
            verbose: Print execution progress
            
        Returns:
            dict with 'status', 'outputs', 'error' keys
        """
        msg_id = str(uuid.uuid4())
        
        if verbose:
            print(f"\n{'='*60}")
            print(f"Executing code (msg_id: {msg_id[:8]}...):")
            print(f"{'='*60}")
            print(code)
            print(f"{'='*60}\n")
        
        # Construct execute_request message
        message = {
            'header': {
                'msg_id': msg_id,
                'username': 'client',
                'session': self.session_id or str(uuid.uuid4()),
                'msg_type': 'execute_request',
                'version': '5.3'
            },
            'parent_header': {},
            'metadata': {},
            'content': {
                'code': code,
                'silent': False,
                'store_history': True,
                'user_expressions': {},
                'allow_stdin': False,
                'stop_on_error': True
            },
            'buffers': [],
            'channel': 'shell'
        }
        
        # Send execute request
        await self.ws.send(json.dumps(message))
        
        # Collect all outputs
        outputs = []
        error = None
        status = 'unknown'
        
        try:
            async with asyncio.timeout(timeout):
                while True:
                    response = await self.ws.recv()
                    msg = json.loads(response)
                    
                    # Only process messages related to our request
                    if msg.get('parent_header', {}).get('msg_id') != msg_id:
                        continue
                    
                    msg_type = msg.get('msg_type')
                    
                    if verbose:
                        print(f"[{msg_type}]", end=' ')
                    
                    # Collect different types of output
                    if msg_type == 'stream':
                        output = {
                            'type': 'stream',
                            'name': msg['content']['name'],  # stdout or stderr
                            'text': msg['content']['text']
                        }
                        outputs.append(output)
                        if verbose:
                            print(f"{output['name']}: {output['text'][:50]}...")
                    
                    elif msg_type == 'execute_result':
                        output = {
                            'type': 'execute_result',
                            'data': msg['content']['data'],
                            'execution_count': msg['content']['execution_count']
                        }
                        outputs.append(output)
                        if verbose:
                            print(f"Result: {str(output['data'])[:50]}...")
                    
                    elif msg_type == 'display_data':
                        output = {
                            'type': 'display_data',
                            'data': msg['content']['data']
                        }
                        outputs.append(output)
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
                    
                    elif msg_type == 'execute_reply':
                        status = msg['content']['status']
                        if verbose:
                            print(f"\n Execution {status}")
                        break
        
        except asyncio.TimeoutError:
            print(f"\n Execution timed out after {timeout}s")
            status = 'timeout'
        
        return {
            'status': status,
            'outputs': outputs,
            'error': error,
            'msg_id': msg_id
        }
    
    async def execute_and_get_output(self, code, output_type='text', timeout=60):
        """
        Execute code and return simplified output.
        
        Args:
            code: Code to execute
            output_type: 'text' (stdout), 'result' (return value), or 'all'
            timeout: Execution timeout
            
        Returns:
            String output or dict of all outputs
        """
        result = await self.execute_code(code, timeout=timeout, verbose=False)
        
        if result['status'] != 'ok':
            if result['error']:
                raise Exception(f"{result['error']['ename']}: {result['error']['evalue']}")
            else:
                raise Exception(f"Execution failed with status: {result['status']}")
        
        if output_type == 'all':
            return result['outputs']
        
        # Extract specific output type
        output_text = []
        
        for output in result['outputs']:
            if output_type == 'text' and output['type'] == 'stream':
                output_text.append(output['text'])
            elif output_type == 'result' and output['type'] == 'execute_result':
                # Get plain text representation
                data = output['data']
                if 'text/plain' in data:
                    output_text.append(data['text/plain'])
                else:
                    output_text.append(str(data))
        
        return ''.join(output_text)
   

    async def http_request(self, method, path, headers=None, body=None, timeout=60):
        """
        Send an HTTP request through the kernel to localhost.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: URL path (e.g., '/api/generate')
            headers: Dict of HTTP headers
            body: Request body (will be JSON encoded if dict)
            timeout: Request timeout
            
        Returns:
            dict with 'status_code', 'headers', 'body' keys
        """
        headers = headers or {}
        
        # Prepare body
        if isinstance(body, dict):
            body_str = json.dumps(body)
            if 'Content-Type' not in headers:
                headers['Content-Type'] = 'application/json'
        else:
            body_str = body or ''
        
        # Escape strings for Python code
        body_escaped = body_str.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
        headers_json = json.dumps(headers)
        
        code = f'''
import requests
import json

try:
    url = f'http://localhost:11434{path}'
    
    response = requests.request(
        method='{method}',
        url=url,
        headers={headers_json},
        data='''{body_escaped}''',
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
        
        output = await self.execute_and_get_output(code, output_type='text', timeout=timeout)
        return json.loads(output.strip())


    async def close(self):
        """Close WebSocket connection"""
        if self.ws:
            await self.ws.close()
            print("close() : WebSocket closed")


