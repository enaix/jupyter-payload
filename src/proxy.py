import client

import asyncio
from aiohttp import web
import json


class JupyterClientProxy(client.JupyterKernelClient):

    async def start_http_proxy_mode(self, host: str, port: int, timeout: int):
        """
        Start HTTP proxy server that forwards requests through Jupyter kernel using async I/O.

        Args:
            host: Host to bind to
            port: Port to bind to
            timeout: Request timeout
        """

        # Queue for pending HTTP requests
        request_queue = asyncio.Queue()

        # Dictionary to track pending responses by sequence ID
        pending_responses = {}

        # Kernel ready flag
        kernel_ready = asyncio.Event()

        # Message ID for kernel execution
        kernel_msg_id = None
        kernel_msg_header = None
        input_request_header = None

        # Start the HTTP request handler code in the kernel
        handler_code = '''
import json
import sys
import aiohttp
import asyncio

# Queue for output to prevent overlap
output_queue = asyncio.Queue()

async def output_worker():
    """Worker that prints responses sequentially"""
    while True:
        try:
            result = await output_queue.get()
            print(json.dumps(result), flush=True)
            output_queue.task_done()
        except Exception as e:
            print(json.dumps({'error': f'Output worker error: {str(e)}', 'seq_id': 'unknown'}), flush=True)

async def handle_request(request_data):
    """Handle a single HTTP request asynchronously"""
    try:
        req = json.loads(request_data)
        seq_id = req['seq_id']
        method = req['method']
        url = req['url']
        headers = req.get('headers', {})
        body = req.get('body')

        # Make the HTTP request using aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=method,
                url=url,
                headers=headers,
                data=body,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response_text = await response.text()

                result = {
                    'seq_id': seq_id,
                    'status_code': response.status,
                    'headers': dict(response.headers),
                    'body': response_text,
                    'ok': response.ok
                }

                await output_queue.put(result)

    except Exception as e:
        result = {
            'seq_id': req.get('seq_id', 'unknown'),
            'status_code': 0,
            'headers': {},
            'body': '',
            'error': str(e),
            'ok': False
        }
        await output_queue.put(result)

async def main():
    """Main async loop"""
    # Start output worker
    output_task = asyncio.create_task(output_worker())

    #print("PROXY_READY", flush=True)

    # List to track request tasks
    request_tasks = []

    while True:
        try:
            # Read request from stdin (blocking, but in async context)
            request_data = await asyncio.get_event_loop().run_in_executor(None, input)

            if not request_data:
                continue

            # Spawn task to handle request asynchronously
            task = asyncio.create_task(handle_request(request_data))
            request_tasks.append(task)

            # Clean up completed tasks
            request_tasks = [t for t in request_tasks if not t.done()]

        except EOFError:
            break
        except Exception as e:
            await output_queue.put({'error': str(e), 'seq_id': 'unknown'})

# Run the async main loop
try:
    asyncio.run(main())
except Exception as e:
    print(json.dumps({'error': f'Fatal: {str(e)}', 'seq_id': 'unknown'}), flush=True)
'''

        print("[main] starting kernel HTTP handler...")
        kernel_msg_id, kernel_msg_header = await self.execute_code_nowait(handler_code, verbose=False, stdin=True)

        # Receive loop - processes messages from kernel
        async def receive_loop():
            while True:
                try:
                    # Read message from kernel
                    msg = await self.read_message(timeout=1, verbose=True)

                    # Only process messages from our handler
                    if msg.get('msg_id') != kernel_msg_id:
                        continue

                    msg_type = msg.get('msg_type')

                    # Handle input request (kernel is ready for next request)
                    if msg_type == 'input_request':
                        if kernel_ready.is_set():
                            print("receive_loop() : warning : another \'input_request\' received while send_loop() is still active")
                        input_request_header = msg["_msg"]['header']  # The payload theoretically should not send more input requests until the send_loop sends the next message
                        kernel_ready.set()  # Requested stdin, notify the send_loop
                        continue

                    # Handle stream output (HTTP response)
                    if msg.get('output'):
                        output = msg['output']
                        if output.get('type') == 'stream' and output.get('name') == 'stdout':
                            text = output.get('text', '').strip()

                            try:
                                # Parse JSON response
                                result = json.loads(text)
                                seq_id = result.get('seq_id')

                                if result.get('error'):
                                    print(f"[payload] error : {result['error']}")
                                    continue

                                if seq_id and seq_id in pending_responses:
                                    # Fulfill the pending response
                                    future = pending_responses.pop(seq_id)
                                    if not future.done():
                                        future.set_result(result)
                                else:
                                    print(f"receive_loop() : zombie request found with seq_id={seq_id}")
                            except json.JSONDecodeError:
                                print(f"receive_loop() : bad json : {text}")

                    # Handle execution errors
                    if msg.get('error'):
                        print(f"[payload] exception : {msg['error']}")

                    if msg.get('status'):
                        if msg['status'] != 'ok':
                            print(f"receive_loop() : payload has unexpectedly exited with status {msg['status']}")
                            raise RuntimeError

                except asyncio.TimeoutError:
                    continue
                #except Exception as e:
                #    print(f"receive_loop() : error: {e}")
                #    await asyncio.sleep(0.1)

        # Send loop - sends requests from queue to kernel
        async def send_loop():
            while True:
                try:
                    # Wait for kernel to be ready
                    await kernel_ready.wait()

                    # Get next request from queue (with timeout to allow checking kernel_ready)
                    try:
                        seq_id, method, url, headers, body = await asyncio.wait_for(
                            request_queue.get(),
                            timeout=0.1
                        )
                    except asyncio.TimeoutError:
                        continue

                    # Prepare request as single JSON object
                    request_data = json.dumps({
                        'seq_id': seq_id,
                        'method': method,
                        'url': url,
                        'headers': headers,
                        'body': body
                    }) + '\n'  # Trigger input() on payload

                    # Send request to kernel as single input_reply
                    await self.send_message(
                        self.new_msg_id(),
                        'input_reply',
                        {'value': request_data},
                        input_request_header,
                        channel='stdin'
                    )

                    # Clear ready flag. We clear it only after processing is finished, so we can verify that there is no new input_request message
                    kernel_ready.clear()

                except TimeoutError:
                    pass
                #except Exception as e:
                #    print(f"send_loop() : error : {e}")
                #    await asyncio.sleep(0.1)

        # Start both loops
        receive_task = asyncio.create_task(receive_loop())
        send_task = asyncio.create_task(send_loop())

        # Wait for kernel to be ready
        await kernel_ready.wait()

        # Request sequence counter
        seq_counter = 0

        async def proxy_handler(request):
            """Handle incoming HTTP requests and proxy through kernel"""
            nonlocal seq_counter

            try:
                # Generate sequence ID
                seq_id = seq_counter
                seq_counter += 1

                # Read request body
                body = await request.read()
                body_str = body.decode('utf-8') if body else ""

                # Create future for response
                response_future = asyncio.Future()
                pending_responses[seq_id] = response_future

                # Queue the request
                await request_queue.put((
                    seq_id,
                    request.method,
                    str(request.url),
                    dict(request.headers),
                    body_str
                ))

                # Wait for response with timeout
                try:
                    result = await asyncio.wait_for(response_future, timeout=timeout)
                except asyncio.TimeoutError:
                    pending_responses.pop(seq_id, None)
                    return web.Response(
                        text=json.dumps({'error': 'Request timeout'}),
                        status=504,
                        content_type='application/json'
                    )

                # Check for errors
                if not result.get('ok', False):
                    if 'error' in result:
                        return web.Response(
                            text=json.dumps({'error': result['error']}),
                            status=500,
                            content_type='application/json'
                        )

                # Return successful response
                return web.Response(
                    text=result['body'],
                    status=result['status_code'],
                    headers=result.get('headers', {})
                )

            except Exception as e:
                print(f"  proxy_handler error: {e}")
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

        print(f"[main] HTTP Proxy Mode started on http://{host}:{port}")
        print(f"[main] All requests will be forwarded through Jupyter kernel")

        return runner, receive_task, send_task
