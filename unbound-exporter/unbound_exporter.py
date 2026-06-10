import time
import requests
import logging
from prometheus_client import start_http_server, Counter, Gauge

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


import os
OPNSENSE_URL = os.environ.get("OPNSENSE_URL")
API_KEY      = os.environ.get("OPNSENSE_API_KEY")
API_SECRET   = os.environ.get("OPNSENSE_API_SECRET")
SCRAPE_INTERVAL = 60
PORT            = 9101

# Counters — cumulative, reset on Unbound restart (Prometheus rate() handles this)
queries         = Counter('unbound_queries_total',              'Total DNS queries')
cache_hits      = Counter('unbound_cache_hits_total',           'Total cache hits')
cache_misses    = Counter('unbound_cache_misses_total',         'Total cache misses')
prefetch        = Counter('unbound_prefetch_total',             'Total prefetch operations')
expired         = Counter('unbound_expired_total',              'Total expired record serves')
servfail        = Counter('unbound_answers_servfail_total',     'Total SERVFAIL answers')
nxdomain        = Counter('unbound_answers_nxdomain_total',     'Total NXDOMAIN answers')
requestlist_exc = Counter('unbound_requestlist_exceeded_total', 'Total times request list exceeded')

# Gauges — point-in-time values
recursion_avg    = Gauge('unbound_recursion_time_avg_seconds',    'Average recursion time in seconds')
recursion_median = Gauge('unbound_recursion_time_median_seconds', 'Median recursion time in seconds')
requestlist_avg  = Gauge('unbound_requestlist_avg',               'Average request list length')
requestlist_max  = Gauge('unbound_requestlist_max',               'Maximum request list length')
msg_cache        = Gauge('unbound_msg_cache_count',               'Message cache entry count')
rrset_cache      = Gauge('unbound_rrset_cache_count',             'RRset cache entry count')

def collect():
    try:
        r = requests.get(OPNSENSE_URL, auth=(API_KEY, API_SECRET),
                         verify=False, timeout=10)
        r.raise_for_status()
        data  = r.json()['data']
        total = data['total']
        n     = total['num']
        rcode = data['num']['answer']['rcode']

        queries._value.set(float(n['queries']))
        cache_hits._value.set(float(n['cachehits']))
        cache_misses._value.set(float(n['cachemiss']))
        prefetch._value.set(float(n['prefetch']))
        expired._value.set(float(n['expired']))
        servfail._value.set(float(rcode['SERVFAIL']))
        nxdomain._value.set(float(rcode['NXDOMAIN']))
        requestlist_exc._value.set(float(total['requestlist']['exceeded']))

        recursion_avg.set(float(total['recursion']['time']['avg']))
        recursion_median.set(float(total['recursion']['time']['median']))
        requestlist_avg.set(float(total['requestlist']['avg']))
        requestlist_max.set(float(total['requestlist']['max']))
        msg_cache.set(float(data['msg']['cache']['count']))
        rrset_cache.set(float(data['rrset']['cache']['count']))

        logging.info(f"Scraped: queries={n['queries']} hits={n['cachehits']} misses={n['cachemiss']}")

    except Exception as e:
        logging.error(f"Scrape failed: {e}")

if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    start_http_server(PORT)
    logging.info(f"Unbound exporter started on port {PORT}")
    while True:
        collect()
        time.sleep(SCRAPE_INTERVAL)
