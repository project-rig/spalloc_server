import sys
import re
import time

from collections import defaultdict

def get_time(time_str):
    return time.mktime(time.strptime(time_str, '%Y-%m-%d %H:%M:%S,%f'))
    
def get_time_in_hours(seconds):
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return "{}:{}:{}".format(int(h), int(m), int(s))

create_job = defaultdict(list)
power_on = dict()
total_jobs = 0
total_time = 0
total_NMPI_jobs = 0
total_NMPI_time = 0
total_machine_test_jobs = 0
total_machine_test_time = 0
total_other_jobs = 0
total_other_time = 0

for filename in sys.argv[1:]:
     with open(filename, "r") as log:
        for line in log:
            match_create = re.search("(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}): create_job\(\(.*\),(\{.*\})\) from (.*)", line)
            
            if match_create is not None:
                date = match_create.group(1)
                dict_string = match_create.group(2)
                ip_address = match_create.group(3)
                dict_object = eval(dict_string)
                create_job[ip_address].append((dict_object["owner"], get_time(date)))
                continue
            
            match_power_on = re.search("(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}): power_job\((\d+),(On|Off)\) from (.*)", line)
            if match_power_on is not None:
                date = match_power_on.group(1)
                job_id = match_power_on.group(2)
                state = match_power_on.group(3)
                ip_address = match_power_on.group(4)
                if state == "On" and len(create_job[ip_address]) > 0:
                    owner, start_time = create_job[ip_address].pop()
                    if len(create_job[ip_address]) == 0:
                        del create_job[ip_address]
                    power_on[job_id] = (owner, start_time)
                continue
            
            match_completed = re.search("(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}): completed shutdown of job (\d+)", line)
            if match_completed is not None:
                date = match_completed.group(1)
                job_id = match_completed.group(2)
                if job_id in power_on:
                    owner, start_time = power_on[job_id]
                    del power_on[job_id]
                    end_time = get_time(date)
                    time_in_seconds = end_time - start_time
                    total_time += time_in_seconds
                    total_jobs += 1
                    if owner == 'NMPI':
                        total_NMPI_time += time_in_seconds
                        total_NMPI_jobs += 1
                    elif owner == 'machine tests':
                        total_machine_test_time += time_in_seconds
                        total_machine_test_jobs += 1
                    else:
                        total_other_time += time_in_seconds
                        total_other_jobs += 1
                continue
            
            match_destroyed = re.search("(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}): destroy_job", line)
            if match_destroyed is None:
                print "Unknown line format for line {}".format(line)
                        
print "Missing power on for {} jobs".format(len(create_job))
print "Missing shutdown for {} jobs".format(len(power_on))

print "Total of {} jobs took {}".format(total_jobs, get_time_in_hours(total_time))
print "Total of {} NMPI jobs took {}".format(total_NMPI_jobs, get_time_in_hours(total_NMPI_time))
print "Total of {} Machine test jobs took {}".format(total_machine_test_jobs, get_time_in_hours(total_machine_test_time))
print "Total of {} Other jobs took {}".format(total_other_jobs, get_time_in_hours(total_other_time))
