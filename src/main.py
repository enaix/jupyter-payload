import sys
import asyncio
import argparse

import tests
import client
import proxy


def test(args):
    print("""
Jupyter Kernel WebSocket Client - Test Suite
============================================

""")
    asyncio.run(tests.test_basic_execution(args.token, args.url))
    asyncio.run(tests.test_http_request(args.token, args.url))
    asyncio.run(tests.test_stdin_interaction(args.token, args.url))
    print("All tests passed")


async def http_proxy(base_args, args):
    cli = proxy.JupyterClientProxy(base_args.url, base_args.token)
    try:
        cli.create_session()
        await cli.connect_websocket()
        try:
            runner = await cli.start_http_proxy_mode(host=args.bind, port=args.port, timeout=args.timeout)
        except asyncio.CancelledError:
            print("shutting down...")
            await runner.cleanup()
            cli.kill_session()

        #while True:
        #    try:
        #        await asyncio.sleep(1)
        #    except KeyboardInterrupt:
        #        print("shutting down...")
        #        await runner.cleanup()
        #        cli.kill_session()

    finally:
        await cli.close()


def main():
    parser = argparse.ArgumentParser(prog="jupyter-payload", formatter_class=argparse.RawDescriptionHelpFormatter, description='Jupyter server payload tool', epilog="""available commands:
  payload   Execute payload
  proxy     Launch a http proxy
  tests     Run simple tests
  tests_inf Run tests indefinitely""")

    def add_default_args(p):
        p.add_argument('-t', '--token', help='jupyter api token')
        p.add_argument('-u', '--url', default="http://localhost:8888", help='jupyter server url')

    def no_token(p):
        p.print_help()
        print("""\n\nNo token was provided. To get your token:
- Run in a Jupyter notebook:
  import os; print(os.environ.get('JUPYTERHUB_API_TOKEN'))
- Or generate at: https://your-jupyterhub/hub/token
- Or press ^C in jupyter server stdin""")
        sys.exit(1)

    def check_if_unknown(unknown, cmd):
        ok = True
        for u in unknown:
            if u != args.command:
                print(f"Unrecognized argument: {f}")
                ok = False
        if not ok:
            sys.exit(1)

    add_default_args(parser)
    parser.add_argument('command', nargs='?', help='command to execute')
    args = parser.parse_args()

    if args.command == "payload":
        raise NotImplementedError

    elif args.command == "proxy":
        pr_parser = argparse.ArgumentParser(prog="jupyter-payload proxy", description='Launch a http proxy on the jupyter server')

        pr_parser.add_argument('-b', '--bind', default='0.0.0.0', help='host to bind to')
        pr_parser.add_argument('-p', '--port', default=8000, help='port to bind to')
        pr_parser.add_argument('--timeout', default=300, help='proxy timeout')
        add_default_args(pr_parser)
        pr_args, unknown = pr_parser.parse_known_args()
        check_if_unknown(unknown, args.command)

        if args.token is None:
            no_token(pr_parser)

        asyncio.run(http_proxy(args, pr_args))

    elif args.command == "tests":
        if args.token is None:
            no_token(parser)
        test(args)
    elif args.command == "tests_inf":
        if args.token is None:
            no_token(parser)
        i = 0
        while True:
            try:
                test(args)
                i+=1
            except KeyboardInterrupt:
                print(f"\nDone {i+1} iterations")
                sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
