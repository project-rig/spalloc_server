import sys
import re
import time
from collections import defaultdict


class Counter(object):
    def __init__(self, name=None):
        self._jobs = 0
        self._time = 0
        self._name = name

    def register(self, time_increment):
        self._jobs += 1
        self._time += time_increment

    def __str__(self):
        if self._name is None:
            return "Total of {} jobs took {}".format(
                self._jobs, get_time_in_hours(self._time))
        else:
            return "Total of {} {} jobs took {}".format(
                self._jobs, self._name, get_time_in_hours(self._time))


def get_time(time_str):
    return time.mktime(time.strptime(time_str, '%Y-%m-%d %H:%M:%S,%f'))


def get_time_in_hours(seconds):
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return "{}:{}:{}".format(int(h), int(m), int(s))


create_job = defaultdict(list)
power_on = dict()
total = Counter()
total_NMPI = Counter("NMPI")
total_machine_test = Counter("Machine test")
total_other = Counter("Other")
other_users = set()


def process_create(match):
    date = match.group(1)
    dict_object = eval(match.group(2))
    ip_address = match.group(3)

    create_job[ip_address].append((dict_object["owner"], get_time(date)))


def process_power_on(match):
    # date isn't used
    # date = match.group(1)
    job_id = match.group(2)
    state = match.group(3)
    ip_address = match.group(4)
    if state == "On" and len(create_job[ip_address]) > 0:
        owner, start_time = create_job[ip_address].pop()
        if len(create_job[ip_address]) == 0:
            del create_job[ip_address]
        power_on[job_id] = (owner, start_time)


def process_completed(match):
    date = match.group(1)
    job_id = match.group(2)
    if job_id in power_on:
        owner, start_time = power_on[job_id]
        del power_on[job_id]
        end_time = get_time(date)
        time_in_seconds = end_time - start_time
        total.register(time_in_seconds)
        if owner == 'NMPI':
            total_NMPI.register(time_in_seconds)
        elif owner == 'machine tests':
            total_machine_test.register(time_in_seconds)
        else:
            total_other.register(time_in_seconds)
            other_users.add(owner)


def scan_logfile(log_file_name):
    with open(log_file_name, "r") as log:
        for line in log:
            match_create = re.search(
                "(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}):"
                " create_job\(\(.*\),(\{.*\})\) from (.*)", line)
            if match_create is not None:
                process_create(match_create)
                continue

            match_power_on = re.search(
                "(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}):"
                " power_job\((\d+),(On|Off)\) from (.*)", line)
            if match_power_on is not None:
                process_power_on(match_power_on)
                continue

            match_completed = re.search(
                "(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}):"
                " completed shutdown of job (\d+)", line)
            if match_completed is not None:
                process_completed(match_completed)
                continue

            match_destroyed = re.search(
                "(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}):"
                " destroy_job", line)
            if match_destroyed is None:
                print("Unknown line format for line {}".format(line))


for filename in sys.argv[1:]:
    scan_logfile(filename)

print("Missing power on for {} jobs".format(len(create_job)))
print("Missing shutdown for {} jobs".format(len(power_on)))

print(str(total))
print(str(total_NMPI))
print(str(total_machine_test))
print(str(total_other))

print("Total of {} Other users".format(len(other_users)))
