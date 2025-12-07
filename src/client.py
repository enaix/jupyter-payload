import asyncio
import websockets
import requests
import json
import uuid
import sys
import os
from aiohttp import web


class JupyterKernelClient:
    def __init__(self, jupyter_url: str, token: str):
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

    async def execute_code_nowait(self, code: str, timeout: int = 60, verbose: bool = True):
        """
        Execute arbitrary code in the kernel and return its message id.

        Args:
            code: Python code to execute
            verbose: Print execution progress

        Returns:
            Message id
        """
        msg_id = str(uuid.uuid4())

        if verbose:
            print(f"\n{'='*60}")
            print(f"Executing code (msg_id: {msg_id[:8]}...):")
            print(f"{'='*60}")
            print(code)
            print(f"{'='*60}\n")

        # Construct execute_request message
        # https://jupyter-client.readthedocs.io/en/stable/messaging.html#the-wire-protocol
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
                'silent': False,  # We may want to set it to True
                'store_history': True, # Same for this flag
                'user_expressions': {},
                'allow_stdin': False, # Currently we run in the non-interactive mode
                'stop_on_error': True
            },
            'buffers': [],
            'channel': 'shell'
        }

        # Send execute request
        await self.ws.send(json.dumps(message))
        return msg_id


    async def collect_execution_output(self, msg_id: str, timeout: int = 60, verbose: bool = True):
        """
        Wait and return the outputs of a code cell.

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
                        received_output = True
                        if verbose:
                            print(f"{output['name']}: {output['text'][:50]}...")

                    elif msg_type == 'execute_result':
                        output = {
                            'type': 'execute_result',
                            'data': msg['content']['data'],
                            'execution_count': msg['content']['execution_count']
                        }
                        outputs.append(output)
                        received_output = True
                        if verbose:
                            print(f"Result: {str(output['data'])[:50]}...")

                    elif msg_type == 'display_data':
                        output = {
                            'type': 'display_data',
                            'data': msg['content']['data']
                        }
                        outputs.append(output)
                        received_output = True
                        if verbose:
                            print(f"Display: {list(output['data'].keys())}")

                    elif msg_type == 'error':
                        error = {
                            'ename': msg['content']['ename'],
                            'evalue': msg['content']['evalue'],
                            'traceback': msg['content']['traceback']
                        }
                        received_output = True
                        if verbose:
                            print(f"\n Error: {error['ename']}: {error['evalue']}")

                    # Main message reply type
                    elif msg_type == 'execute_reply':
                        status = msg['content']['status']

                        # if status is ok, then msg contains "payload" and "user_expressions" dicts
                        got_exec_reply = True
                        if verbose:
                            print(f"\nExecution {status}")

        except asyncio.TimeoutError:
            print(f"\nExecution timed out after {timeout}s")
            status = 'timeout'
    
        return {
            'status': status,
            'outputs': outputs,
            'error': error,
            'msg_id': msg_id
        }


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
        msg_id = await self.execute_code_nowait(code, verbose)
        result = await self.collect_execution_output(msg_id, timeout, verbose)
        return result


    async def execute_and_get_output(self, code: str, output_type: str = 'text', timeout: int = 60, verbose: bool = False):
        """
        Execute code and return simplified output.
        
        Args:
            code: Code to execute
            output_type: 'text' (stdout), 'result' (return value), or 'all'
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
        
        # Extract specific output type
        output_text = []
        
        for output in result['outputs']:
            if output_type == 'text' and output['type'] == 'stream':
                output_text.append(output['text'])
            elif output_type == 'text' and output['type'] == 'execute_result':
                # Get plain text representation
                data = output['data']
                if 'text/plain' in data:
                    output_text.append(str(data['text/plain']))
                else:
                    output_text.append(str(data))
            # TODO change output_type to something more meaningful

        return ''.join(output_text)
   

    async def http_request(self, method: str, url: str, headers: dict = None, body = None, timeout: int = 60, verbose: bool = True):
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
        headers = headers or {}
        
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


    async def start_http_proxy_mode(self, host: str, port: int, timeout: int):
        """
        Start HTTP proxy server that forwards requests through Jupyter kernel.

        Args:
            host: Host to bind to
            port: Port to bind to
        """

        async def proxy_handler(request):
            """Handle incoming HTTP requests and proxy through kernel"""
            try:
                # Read request body
                body = await request.read()
                body_str = body.decode('utf-8') if body else None

                # Forward to kernel
                result = await self.http_request(
                    method=request.method,
                    path=request.url,
                    headers=dict(request.headers),
                    body=body_str,
                    timeout=timeout
                )

                if not result.get('ok', False):
                    print("proxy_handler() : http payload error : ", end="")
                    if 'error' in result:
                        print(result['error'])
                        return web.Response(
                            text=json.dumps({'error': result['error']}),
                            status=500,
                            content_type='application/json'
                        )
                    else:
                        print("unknown")

                # Return response
                return web.Response(
                    text=result['body'],
                    status=result['status_code'],
                    headers=result.get('headers', {})
                )

            except Exception as e:
                print(f"proxy_handler() : exception : {e}")
                return web.Response(
                    text=json.dumps({'error': str(e)}),
                    status=500,
                    content_type='application/json'
                )

        # Create web app
        app = web.Application()
        app.router.add_route('*', '/{path:.*}', proxy_handler)

        # Start server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        print(f"  HTTP Proxy Mode started on http://{host}:{port}")
        print(f"  All requests will be forwarded through Jupyter kernel")

        return runner


    async def close(self):
        """Close WebSocket connection"""
        if self.ws:
            await self.ws.close()
            print("close() : WebSocket closed")


