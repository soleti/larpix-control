'''
Run basic noise tests for chips
  Note: Reset chips before each test.
'''

from __future__ import absolute_import
from larpix.quickstart import quickcontroller
from larpix.quickstart import disable_chips
from larpix.larpix import (flush_logger, PacketCollection)
import math
import time
import json

def find_channel_thresholds(controller=None, board='pcb-1', chip_idx=0,
                            channel_list=range(32),
                            saturation_level=1000, threshold_min_coarse=20,
                            threshold_max_coarse=40, threshold_step_coarse=1,
                            trim_min=0, trim_max=31, trim_step=1, run_time=0.1):
    # Create controller and initialize chips to appropriate state
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
        print('  created controller')
    # Run coarse scan to determine global thresholds
    coarse_scan_data = scan_threshold(controller, board, chip_idx, channel_list,
                                      threshold_min_coarse, threshold_max_coarse,
                                      threshold_step_coarse, saturation_level, run_time)
    coarse_scan_results = examine_global_scan(coarse_scan_data, saturation_level)
    print('  coarse scan complete')
    mean_global_threshold = int(coarse_scan_results['mean_thresh'])
    # Run fine scan to determine pixel thresholds
    fine_scan_data = scan_trim(controller, board, chip_idx, channel_list,
                                  trim_min, trim_max, trim_step, saturation_level,
                                  mean_global_threshold, run_time)
    fine_scan_results = examine_fine_scan(fine_scan_data, saturation_level)
    print('  fine scan complete')
    pixel_trim_thresholds = {}
    for key in fine_scan_results:
        if isinstance(key, int):
            pixel_trim_thresholds[key] = fine_scan_results[key]['saturation_trim']
        elif key is 'chan_level_too_high':
            for channel in fine_scan_results['chan_level_too_high']:
                pixel_trim_thresholds[channel] = trim_max
        elif key is 'chan_level_too_low':
            for channel in fine_scan_results['chan_level_too_low']:
                pixel_trim_thresholds[channel] = trim_min

    if close_controller:
        controller.serial_close()
    return (mean_global_threshold, pixel_trim_thresholds, coarse_scan_results,
            fine_scan_results)

def simultaneous_scan_trim(controller=None, board='pcb-5', chip_idx=0,
                           channel_list=range(32), 
                           trim_min=0, trim_max=31, trim_step=1, saturation_level=1000, 
                           max_level=1200,
                           global_threshold=30, run_time=0.1):
    # Create controller and initialize chips to appropriate state
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
        print('  created controller')
    # Get chip under test
    chip = controller.chips[chip_idx]
    results = {}
    global_threshold_orig = chip.config.global_threshold
    pixel_trim_thresholds_orig = chip.config.pixel_trim_thresholds
    channel_mask_orig = chip.config.channel_mask[:]
    print('testing chip',chip.chip_id)
    # Configure chip for one channel operation
    chip.config.global_threshold = global_threshold
    chip.config.disable_channels()
    controller.write_configuration(chip,range(52,56))
    time.sleep(1)
    chip.config.enable_channels(channel_list)
    chip.config.reset_cycles = 4092
    print('  writing config')
    controller.write_configuration(chip,range(60,62))
    controller.write_configuration(chip,[32,52,53,54,55])
    print('  reading config')
    controller.read_configuration(chip)
    print('  set mask')
    # Prepare to scan
    controller.run(5,'clear buffer')
    n_packets = []
    adc_means = []
    adc_rmss = []
    channel_trims = {}
    channel_npackets = {}
    scan_completed = {}
    for channel in channel_list:
        channel_trims[channel] = []
        channel_npackets[channel] = []
        scan_completed[channel] = False
    next_trim = trim_max
    while next_trim >= trim_min:
        # Set global coarse threshold
        for channel in channel_list:
            if not scan_completed[channel]:
                chip.config.pixel_trim_thresholds[channel] = next_trim
                channel_trims[channel].append(next_trim)
        controller.write_configuration(chip,range(0,32))
        print('    set trim %d' % next_trim)
        print('    clear buffer (quick)')
        controller.run(0.1,'clear buffer')
        del controller.reads[-1]
        #if threshold == thresholds[0]:
        if len(controller.reads) > 0 and len(controller.reads[-1]) > 0:
        #if True:
            # Flush buffer for first cycle
            print('    clear buffer (slow)')
            controller.run(2,'clear buffer')
        controller.reads = []
        # Collect data
        print('    reading')
        controller.run(run_time,'scan trim')
        print('    done reading')
        # Process data
        packets = controller.reads[-1]
        packets_by_channel = {}
        for channel in channel_list:
            packets_by_channel[channel] = [packet for packet in controller.reads[-1]
                                           if packet.chipid ==chip.chip_id and 
                                           packet.channel_id == channel]

        if any([len(packets_by_channel[channel]) for channel in channel_list])>max_level:
            # turn off noisy channels
            for channel in channel_list:
                if len(packets_by_channel[channel])>max_level:
                    chip.config.disable_channels([channel])
                    controller.write_configuration(chip,range(52,56),write_read=1)
                    del controller.reads[-1]
            continue
        else:
            next_trim -= trim_step

        for channel in channel_list:
            if len(packets_by_channel[channel])>0:
                channel_npackets[channel].append(len(packets_by_channel[channel]))
                print('  %d %d %d %d' % (channel, channel_trims[channel][-1],
                                         len(packets_by_channel[channel]),
                                         scan_completed[channel]))
            if len(packets_by_channel[channel])>=saturation_level:
                scan_completed[channel] = True
            
        if all([scan_completed[channel] for channel in scan_completed]):
            break
    for channel in channel_list:
        results[channel] = {'trims':channel_trims[channel],
                            'npackets':channel_npackets[channel],
                            'complete':scan_completed[channel]}


    # Restore original global threshold and channel mask
    chip.config.pixel_trim_thresholds = pixel_trim_thresholds_orig
    chip.config.global_threshold = global_threshold_orig
    controller.write_configuration(chip,range(0,33))
    chip.config.channel_mask = channel_mask_orig
    controller.write_configuration(chip,[52,53,54,55])
    if close_controller:
        controller.serial_close()
    return results

def simultaneous_scan_trim_with_communication(controller=None, board='pcb-5', chip_idx=0,
                                              channel_list=range(32), 
                                              trim_min=0, trim_max=31, trim_step=1,
                                              saturation_level=10,
                                              max_level=100, writes=100,
                                              global_threshold=30, run_time=0.1):
    # Create controller and initialize chips to appropriate state
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
        print('  created controller')
    # Get chip under test
    chip = controller.chips[chip_idx]
    results = {}
    global_threshold_orig = chip.config.global_threshold
    pixel_trim_thresholds_orig = chip.config.pixel_trim_thresholds
    channel_mask_orig = chip.config.channel_mask[:]
    print('testing chip',chip.chip_id)
    # Configure chip for one channel operation
    chip.config.global_threshold = global_threshold
    chip.config.disable_channels()
    controller.write_configuration(chip,range(52,56))
    controller.run(0.1,'clear buffer')
    chip.config.enable_channels(channel_list)
    chip.config.reset_cycles = 4092
    print('  writing config')
    controller.write_configuration(chip,range(60,62)) # reset cycles
    controller.write_configuration(chip,[32,52,53,54,55])
    print('  reading config')
    controller.read_configuration(chip)
    print('  set mask')
    # Prepare to scan
    controller.run(5,'clear buffer')
    n_packets = []
    adc_means = []
    adc_rmss = []
    disabled_channels = []
    channel_trims = {}
    channel_npackets = {}
    scan_completed = {}
    for channel in channel_list:
        channel_trims[channel] = []
        channel_npackets[channel] = []
        scan_completed[channel] = False
    next_trim = trim_max
    while next_trim >= trim_min:
        # Set global coarse threshold
        for channel in channel_list:
            if not scan_completed[channel]:
                chip.config.pixel_trim_thresholds[channel] = next_trim
                channel_trims[channel].append(next_trim)
        controller.write_configuration(chip,range(0,32))
        print('    set trim %d' % next_trim)
        print('    clear buffer (quick)')
        controller.run(0.1,'clear buffer')
        del controller.reads[-1]
        #if threshold == thresholds[0]:
        if len(controller.reads) > 0 and len(controller.reads[-1]) > 0:
        #if True:
            # Flush buffer for first cycle
            print('    clear buffer (slow)')
            controller.run(2,'clear buffer')
        controller.reads = []
        # Collect data
        print('    writing and reading')
        for write in range(writes):
            controller.write_configuration(chip, 32, write_read=run_time)
        print('    done reading')

        # Process data
        reads = controller.reads[-writes:]
        packets = PacketCollection([packet for read in reads for packet in read])
        print('    read %d' % len(packets))
        packets_by_channel = {}
        for channel in channel_list:
            packets_by_channel[channel] = []
        for packet in packets:
            if packet.chipid == chip.chip_id:
                packets_by_channel[packet.channel_id] += [packet]
        if any([len(packets_by_channel[channel])>max_level for channel in channel_list]):
            # turn off noisy channels
            for channel in channel_list:
                if len(packets_by_channel[channel])>max_level:
                    print('    disabling ch%d' % channel)
                    chip.config.disable_channels([channel])
                    controller.write_configuration(chip,range(52,56),write_read=1)
                    del controller.reads[-1]
                    disabled_channels.append(channel)
            continue
        else:
            next_trim -= trim_step

        for channel in channel_list:
            if len(packets_by_channel[channel])>0:
                channel_npackets[channel].append(len(packets_by_channel[channel]))
                print('  %d %d %d %d' % (channel, channel_trims[channel][-1],
                                         len(packets_by_channel[channel]),
                                         scan_completed[channel]))
            if len(packets_by_channel[channel])>=saturation_level:
                scan_completed[channel] = True
            
        if all([scan_completed[channel] for channel in scan_completed]):
            break
    for channel in channel_list:
        results[channel] = {'trims':channel_trims[channel],
                            'npackets':channel_npackets[channel],
                            'complete':scan_completed[channel]}
    results['disabled_channels'] = disabled_channels

    # Restore original global threshold and channel mask
    chip.config.pixel_trim_thresholds = pixel_trim_thresholds_orig
    chip.config.global_threshold = global_threshold_orig
    controller.write_configuration(chip,range(0,33))
    chip.config.channel_mask = channel_mask_orig
    controller.write_configuration(chip,[52,53,54,55])
    if close_controller:
        controller.serial_close()
    return results

def scan_trim(controller=None, board='pcb-5', chip_idx=0, channel_list=range(32), 
              trim_min=0, trim_max=31, trim_step=1, saturation_level=1000, 
              global_threshold=30, run_time=0.1):
    # Create controller and initialize chips to appropriate state
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
        print('  created controller')
    # Get chip under test
    chip = controller.chips[chip_idx]
    results = {}
    global_threshold_orig = chip.config.global_threshold
    pixel_trim_thresholds_orig = chip.config.pixel_trim_thresholds
    channel_mask_orig = chip.config.channel_mask[:]
    print('testing chip',chip.chip_id)
    for channel in channel_list:
        print('testing channel',channel)
        # Configure chip for one channel operation
        chip.config.global_threshold = global_threshold
        chip.config.channel_mask = [1,]*32
        chip.config.channel_mask[channel] = 0
        chip.config.reset_cycles = 4092
        print('  writing config')
        controller.write_configuration(chip,range(60,62))
        controller.write_configuration(chip,[32,52,53,54,55])
        print('  reading config')
        controller.read_configuration(chip)
        print('  set mask')
        # Scan thresholds
        trims = range(trim_min,
                      trim_max+1,
                      trim_step)
        # Scan from high to low
        trims.reverse()
        # Prepare to scan
        controller.run(5,'clear buffer')
        n_packets = []
        adc_means = []
        adc_rmss = []
        for trim in trims:
            # Set global coarse threshold
            chip.config.pixel_trim_thresholds[channel] = trim
            controller.write_configuration(chip,range(0,32))
            print('    set threshold')
            print('    clear buffer (quick)')
            controller.run(0.1,'clear buffer')
            del controller.reads[-1]
            #if threshold == thresholds[0]:
            if len(controller.reads) > 0 and len(controller.reads[-1]) > 0:
            #if True:
                # Flush buffer for first cycle
                print('    clearing buffer')
                time.sleep(0.2)
                controller.run(2,'clear buffer')
                time.sleep(0.2)
            controller.reads = []
            # Collect data
            print('    reading')
            controller.run(run_time,'scan trim')
            print('    done reading')
            # Process data
            packets = controller.reads[-1]
            #[packet for packet in controller.reads[-1]
                      # if packet.chipid ==chip.chip_id and packet.channel_id == channel]
            adc_mean = 0
            adc_rms = 0
            if len(packets)>0:
                print('    processing packets: %d' % len(packets))
                adcs = [p.dataword for p in packets 
                        if p.chipid == chip.chip_id and p.channel_id == channel]
                if len(adcs) > 0:
                    adc_mean = sum(adcs)/float(len(adcs))
                    adc_rms = (sum([abs(adc-adc_mean) for adc in adcs])
                               / float(len(adcs)))
            n_packets.append(len(packets))
            adc_means.append(adc_mean)
            adc_rmss.append(adc_rms)
            print(    '%d %d %0.2f %0.4f' % (trim, len(packets),
                                             adc_mean, adc_rms))
            if len(packets)>saturation_level:
                # Stop scanning if saturation level is hit.
                break
        results[channel] = [trims[:], n_packets[:],
                            adc_means[:], adc_rmss[:]]
    # Restore original global threshold and channel mask
    chip.config.pixel_trim_thresholds = pixel_trim_thresholds_orig
    chip.config.global_threshold = global_threshold_orig
    controller.write_configuration(chip,range(0,33))
    chip.config.channel_mask = channel_mask_orig
    controller.write_configuration(chip,[52,53,54,55])
    if close_controller:
        controller.serial_close()
    return results

def scan_threshold(controller=None, board='pcb-5', chip_idx=0,
                   channel_list=range(32), threshold_min_coarse=26,
                   threshold_max_coarse=37, threshold_step_coarse=1,
                   saturation_level=1000, run_time=0.1):
    '''Scan the signal rate versus channel threshold'''
    # Create controller and initialize chips to appropriate state
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
        print('  created controller')
    # Get chip under test
    chip = controller.chips[chip_idx]
    results = {}
    global_threshold_orig = chip.config.global_threshold
    channel_mask_orig = chip.config.channel_mask[:]
    print('testing chip',chip.chip_id)
    for channel in channel_list:
        print('testing channel',channel)
        # Configure chip for one channel operation
        chip.config.channel_mask = [1,]*32
        chip.config.channel_mask[channel] = 0
        print('  writing config')
        controller.write_configuration(chip,[52,53,54,55])
        print('  reading config')
        controller.read_configuration(chip)
        print('  set mask')
        # Scan thresholds
        thresholds = range(threshold_min_coarse,
                           threshold_max_coarse+1,
                           threshold_step_coarse)
        # Scan from high to low
        thresholds.reverse()
        # Prepare to scan
        n_packets = []
        adc_means = []
        adc_rmss = []
        for threshold in thresholds:
            # Set global coarse threshold
            chip.config.global_threshold = threshold
            controller.write_configuration(chip,32)
            print('    set threshold')
            print('    clear buffer (quick)')
            controller.run(0.1,'clear buffer')
            del controller.reads[-1]
            #if threshold == thresholds[0]:
            if len(controller.reads) > 0 and len(controller.reads[-1]) > 0:
            #if True:
                # Flush buffer for first cycle
                print('    clear buffer (slow)')
                time.sleep(0.2)
                controller.run(2,'clear buffer')
                time.sleep(0.2)
            controller.reads = []
            # Collect data
            print('    reading')
            controller.run(run_time,'scan threshold')
            print('    done reading')
            # Process data
            packets = controller.reads[-1]
            adc_mean = 0
            adc_rms = 0
            if len(packets)>0:
                print('    processing packets: %d' % len(packets))
                adcs = [p.dataword for p in packets 
                        if p.chipid == chip.chip_id and p.channel_id == channel]
                if len(adcs) > 0:
                    adc_mean = sum(adcs)/float(len(adcs))
                    adc_rms = (sum([abs(adc-adc_mean) for adc in adcs])
                               / float(len(adcs)))
            n_packets.append(len(packets))
            adc_means.append(adc_mean)
            adc_rmss.append(adc_rms)
            print(    '%d %d %0.2f %0.4f' % (threshold, len(packets),
                                             adc_mean, adc_rms))
            if len(packets)>=saturation_level:
                # Stop scanning if saturation level is hit.
                break
        results[channel] = [thresholds[:], n_packets[:],
                            adc_means[:], adc_rmss[:]]
    # Restore original global threshold and channel mask
    chip.config.global_threshold = global_threshold_orig
    controller.write_configuration(chip,32)
    chip.config.channel_mask = channel_mask_orig
    controller.write_configuration(chip,[52,53,54,55])
    if close_controller:
        controller.serial_close()
    return results

def scan_threshold_with_communication(controller=None, board='pcb-1', chip_idx=0,
                                      channel_list=range(32), threshold_min_coarse=26,
                                      threshold_max_coarse=37, threshold_step_coarse=1,
                                      saturation_level=1000, run_time=0.1):
    '''Scan the signal rate versus channel threshold while writing to chip registers'''
    # Create controller and initialize chips to appropriate state
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
        print('  created controller')
    # Get chip under test
    chip = controller.chips[chip_idx]
    results = {}
    global_threshold_orig = chip.config.global_threshold
    channel_mask_orig = chip.config.channel_mask[:]
    print('testing chip',chip.chip_id)
    for channel in channel_list:
        print('testing channel',channel)
        # Configure chip for one channel operation
        chip.config.channel_mask = [1,]*32
        chip.config.channel_mask[channel] = 0
        print('  writing config')
        controller.write_configuration(chip,[52,53,54,55])
        print('  reading config')
        controller.read_configuration(chip)
        print('  set mask')
        # Scan thresholds
        thresholds = range(threshold_min_coarse,
                           threshold_max_coarse+1,
                           threshold_step_coarse)
        # Scan from high to low
        thresholds.reverse()
        # Prepare to scan
        n_packets = []
        adc_means = []
        adc_rmss = []
        for threshold in thresholds:
            # Set global coarse threshold
            chip.config.global_threshold = threshold
            controller.write_configuration(chip,32)
            print('    set threshold')
            print('    clear buffer (quick)')
            controller.run(0.1,'clear buffer')
            del controller.reads[-1]
            #if threshold == thresholds[0]:
            if len(controller.reads) > 0 and len(controller.reads[-1]) > 0:
            #if True:
                # Flush buffer for first cycle
                print('    clear buffer (slow)')
                time.sleep(0.2)
                controller.run(2,'clear buffer')
                time.sleep(0.2)
            controller.reads = []
            # Collect data
            print('    writing and reading')
            controller.write_configuration(chip,32,write_read=run_time)
            print('    done reading')
            # Process data
            packets = controller.reads[-1]
            adc_mean = 0
            adc_rms = 0
            if len(packets)>0:
                print('    processing packets: %d' % len(packets))
                adcs = [p.dataword for p in packets 
                        if p.chipid == chip.chip_id and p.channel_id == channel]
                if len(adcs) > 0:
                    adc_mean = sum(adcs)/float(len(adcs))
                    adc_rms = (sum([abs(adc-adc_mean) for adc in adcs])
                               / float(len(adcs)))
            n_packets.append(len(packets))
            adc_means.append(adc_mean)
            adc_rmss.append(adc_rms)
            print(    '%d %d %0.2f %0.4f' % (threshold, len(packets),
                                             adc_mean, adc_rms))
            if len(packets)>=saturation_level:
                # Stop scanning if saturation level is hit.
                break
        results[channel] = [thresholds[:], n_packets[:],
                            adc_means[:], adc_rmss[:]]
    # Restore original global threshold and channel mask
    chip.config.global_threshold = global_threshold_orig
    controller.write_configuration(chip,32)
    chip.config.channel_mask = channel_mask_orig
    controller.write_configuration(chip,[52,53,54,55])
    if close_controller:
        controller.serial_close()
    return results

def test_leakage_current(controller=None, chip_idx=0, board='pcb-5', reset_cycles=4096,
                         global_threshold=125, trim=16, run_time=1, channel_list=range(32)):
    '''Sets chips to high threshold and counts number of triggers'''
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
    
    chip = controller.chips[0]
    print('initial configuration for chip %d' % chip.chip_id)
    chip.config.global_threshold = global_threshold
    chip.config.pixel_trim_thresholds = [trim] * 32
    if reset_cycles is None:
        chip.config.periodic_reset = 0
    else:
        chip.config.reset_cycles = reset_cycles
        chip.config.periodic_reset = 1
    chip.config.disable_channels()
    controller.write_configuration(chip)

    return_data = {
        'channel':[],
        'n_packets':[],
        'run_time':[],
        'rate': [],
        }
    print('clear buffer')
    controller.run(2,'clear buffer')
    del controller.reads[-1]
    for channel in channel_list:
        chip.config.disable_channels()
        chip.config.enable_channels([channel])
        controller.write_configuration(chip,range(52,56))
        # flush buffer
        print('clear buffer')
        controller.run(0.1,'clear buffer')
        del controller.reads[-1]
        # run for run_time
        print('begin test (runtime = %.1f, channel = %d)' % (run_time, channel))
        controller.run(run_time,'leakage current test')
        read = controller.reads[-1]
        return_data['channel'] += [channel]
        return_data['n_packets'] += [len(read)]
        return_data['run_time'] += [run_time]
        return_data['rate'] += [float(len(read))/run_time]
        print('channel %2d: %.2f' % (channel, return_data['rate'][-1]))
    mean_rate = sum(return_data['rate'])/len(return_data['rate'])
    rms_rate = sum(abs(rate - mean_rate) 
                   for rate in return_data['rate'])/len(return_data['rate'])
    print('chip mean: %.3f, rms: %.3f' % (mean_rate, rms_rate))
    if close_controller:
        controller.serial_close()
    return return_data

def pulse_chip(controller, chip, dac_level):
    '''Issue one pulse to specific chip'''
    chip.config.csa_testpulse_dac_amplitude = dac_level
    controller.write_configuration(chip,46,write_read=0.1)
    return controller.reads[-1]

def noise_test_all_chips(n_pulses=1000, pulse_channel=0, pulse_dac=6, threshold=40,
                         controller=None, testpulse_dac_max=235, testpulse_dac_min=40,
                         trim=0, board='pcb-5', reset_cycles=4096, csa_recovery_time=0.1,
                         reset_dac_time=1):
    '''Run noise_test_internal_pulser on all available chips'''
    # Create controller and initialize chip,s to appropriate state
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)

    for chip_idx in range(len(controller.chips)):
        chip_threshold = threshold
        chip_pulse_dac = pulse_dac
        if isinstance(threshold, list):
            chip_threshold = threshold[chip_idx]
        if isinstance(pulse_dac, list):
            chip_pulse_dac = pulse_dac[chip_idx]
        noise_test_internal_pulser(board=board, chip_idx=chip_idx, n_pulses=n_pulses,
                                   pulse_channel=pulse_channel, reset_cycles=reset_cycles,
                                   pulse_dac=chip_pulse_dac, threshold=chip_threshold,
                                   controller=controller, csa_recovery_time=csa_recovery_time,
                                   testpulse_dac_max=testpulse_dac_max,
                                   reset_dac_time=reset_dac_time,
                                   testpulse_dac_min=testpulse_dac_min, trim=trim)
    result = controller.reads
    if close_controller:
        controller.serial_close()
    return result

def noise_test_external_pulser(board='pcb-5', chip_idx=0, run_time=10,
                               channel_list=range(32), global_threshold=200,
                               controller=None):
    '''Scan through channels with external trigger enabled - report adc width'''
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
    disable_chips(controller)
    # Get chip under test
    chip = controller.chips[chip_idx]
    print('initial configuration for chip %d' % chip.chip_id)
    chip.config.global_threshold = global_threshold
    controller.write_configuration(chip,32)
    adc_values = {}
    mean = {}
    std_dev = {}
    for channel in channel_list:
        print('test channel %d' % channel)
        print('  clear buffer (slow)')
        controller.run(1,'clear buffer')
        chip.config.enable_channels([channel])
        chip.config.enable_external_trigger([channel])
        controller.write_configuration(chip,range(52,60))
        print('  clear buffer (quick)')
        controller.run(0.1,'clear buffer')
        print('  run')
        controller.run(run_time,'collect data')
        adc_values[channel] = [packet.dataword for packet in controller.reads[-1]
                               if packet.packet_type == packet.DATA_PACKET and
                               packet.chipid == chip.chip_id and 
                               packet.channel_id == channel]
        chip.config.disable_channels()
        chip.config.disable_external_trigger()
        controller.write_configuration(chip,range(52,60))
        mean[channel] = float(sum(adc_values[channel]))/len(adc_values[channel])
        std_dev[channel] = math.sqrt(sum([float(value)**2 for value in adc_values[channel]])/len(adc_values[channel]) - mean[channel]**2)
        print('%d  %f  %f' % (channel, mean[channel], std_dev[channel]))
    print('summary (channel, mean, std dev):')
    for channel in channel_list:
        print('%d  %f  %f' % (channel, mean[channel], std_dev[channel]))

    flush_logger()
    if close_controller:
        controller.serial_close()
    return (adc_values, mean, std_dev)

def noise_test_low_threshold(board='pcb-5', chip_idx=0, run_time=1,
                             channel_list=range(32), global_threshold=0,
                             controller=None):
    '''Scan through channels at low threshold - report adc width'''
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
    disable_chips(controller)
    # Get chip under test
    chip = controller.chips[chip_idx]
    print('initial configuration for chip %d' % chip.chip_id)
    chip.config.global_threshold = global_threshold
    controller.write_configuration(chip,32)
    adc_values = {}
    mean = {}
    std_dev = {}
    for channel in channel_list:
        print('test channel %d' % channel)
        print('clear buffer (slow)')
        controller.run(1,'clear buffer')
        chip.config.enable_channels([channel])
        controller.write_configuration(chip,range(52,56))
        print('clear buffer (quick)')
        controller.run(0.1,'clear buffer')
        controller.run(run_time,'collect data')
        adc_values[channel] = [packet.dataword for packet in controller.reads[-1]
                               if packet.packet_type == packet.DATA_PACKET and
                               packet.chipid == chip.chip_id and 
                               packet.channel_id == channel]
        chip.config.disable_channels()
        controller.write_configuration(chip,range(52,56))
        mean[channel] = float(sum(adc_values[channel]))/len(adc_values[channel])
        std_dev[channel] = math.sqrt(sum([float(value)**2 for value in adc_values[channel]])/len(adc_values[channel]) - mean[channel]**2)
        print('%d  %f  %f' % (channel, mean[channel], std_dev[channel]))
    print('summary (channel, mean, std dev):')
    for channel in channel_list:
        print('%d  %f  %f' % (channel, mean[channel], std_dev[channel]))

    flush_logger()
    if close_controller:
        controller.serial_close()
    return (adc_values, mean, std_dev)

def noise_test_internal_pulser(board='pcb-5', chip_idx=0, n_pulses=1000,
                               pulse_channel=0, pulse_dac=6, threshold=40,
                               controller=None, testpulse_dac_max=235,
                               testpulse_dac_min=40, trim=0, reset_cycles=4096,
                               csa_recovery_time=0.1, reset_dac_time=1):
    '''Use cross-trigger from one channel to evaluate noise on other channels'''
    # Create controller and initialize chips to appropriate state
    close_controller = False
    if controller is None:
        close_controller = True
        controller = quickcontroller(board)
        #disable_chips(controller)
    # Get chip under test
    chip = controller.chips[chip_idx]
    print('initial configuration for chip %d' % chip.chip_id)
    # Configure chip for pulsing one channel
    chip.config.csa_testpulse_enable[pulse_channel] = 0 # Connect
    controller.write_configuration(chip,[42,43,44,45])
    # Initialize DAC level, and issuing cross-triggers
    chip.config.csa_testpulse_dac_amplitude = testpulse_dac_max
    controller.write_configuration(chip,46)
    # Set initial threshold, and enable cross-triggers
    chip.config.global_threshold = threshold
    chip.config.pixel_trim_thresholds = [31] * 32
    chip.config.pixel_trim_thresholds[pulse_channel] = trim
    chip.config.cross_trigger_mode = 1
    chip.config.reset_cycles = reset_cycles
    controller.write_configuration(chip,range(60,63)) # reset cycles
    controller.write_configuration(chip,range(32)) # trim
    controller.write_configuration(chip,[32,47]) # global threshold / xtrig
    #chip.config.enable_analog_monitor(pulse_channel)
    #controller.write_configuration(chip,range(38,42)) # monitor
    #chip.config.enable_channels([pulse_channel]) # enable pulse channel
    #controller.write_configuration(chip,range(52,56)) # channel mask
    print('initial configuration done')
    # Pulse chip n times
    dac_level = testpulse_dac_max
    lost = 0
    extra = 0
    print('clear buffer')
    controller.run(0.1, 'clear buffer')
    del controller.reads[-1]
    time.sleep(csa_recovery_time)
    for pulse_idx in range(n_pulses):
        if dac_level < (testpulse_dac_min + pulse_dac):
            # Reset DAC level if it is too low to issue pulse
            chip.config.csa_testpulse_dac_amplitude = testpulse_dac_max
            controller.write_configuration(chip,46)
            time.sleep(reset_dac_time) # Wait for front-end to settle
            print('reset DAC value')
            # FIXME: do we need to flush buffer here?
            dac_level = testpulse_dac_max
        # Issue pulse
        dac_level -= pulse_dac  # Negative DAC step mimics electron arrival
        time.sleep(csa_recovery_time)
        result = pulse_chip(controller, chip, dac_level)
        if len(result) - 32 > 0:
            extra += 1
        elif len(result) - 32 < 0:
            lost += 1
        print('pulse: %4d, received: %4d, DAC: %4d' % (pulse_idx, len(result), dac_level))

    # Reset DAC level, and disconnect channel
    chip.config.disable_testpulse() # Disconnect
    controller.write_configuration(chip,[42,43,44,45]) # testpulse
    chip.config.csa_testpulse_dac_amplitude = 0
    controller.write_configuration(chip,46) # dac amplitude
    chip.config.cross_trigger_mode = 0
    chip.config.global_threshold = 255
    controller.write_configuration(chip,[32,47]) # global threshold / xtrig
    chip.config.pixel_trim_thresholds = [16] * 32
    controller.write_configuration(chip,range(32)) # trim
    #chip.config.disable_analog_monitor()
    #controller.write_configuration(chip,range(38,42)) # monitor

    # Keep a handle to chip data, and return
    result = controller.reads
    flush_logger()
    if close_controller:
        controller.serial_close()
    print('Pulses with # trigs > 1: %4d, Missed trigs: %4d' % (extra, lost))
    return result

def scan_threshold_with_pulse(controller=None, board='pcb-1', chip_idx=0,
                              channel_list=range(32), max_acceptable_efficiency=1.5,
                              min_acceptable_efficiency=0.5, n_pulses=100, dac_pulse=6,
                              testpulse_dac_max=235, testpulse_dac_min=229, reset_cycles=4096,
                              threshold_max=40, threshold_min=20, threshold_step=1):
    ''' Pulse channels with test pulse to determine the minimum threshold for
    triggering at least a specified efficiency '''
    close_controller = False
    if not controller:
        # Create controller and initialize chips to appropriate state
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
    chip = controller.chips[chip_idx]
    controller.run(5, 'clear buffer')
    results = {}
    for channel_idx, channel in enumerate(channel_list):
        print('configuring chip %d channel %d' % (chip.chip_id, channel))
        # Configure chip for pulsing one channel
        chip.config.csa_testpulse_enable = [1]*32 # Disconnect any channels
        chip.config.csa_testpulse_enable[channel] = 0 # Connect
        controller.write_configuration(chip,[42,43,44,45])
        # Initialize DAC level
        chip.config.csa_testpulse_dac_amplitude = testpulse_dac_max
        controller.write_configuration(chip,46)
        # Enable channel
        chip.config.disable_channels()
        chip.config.enable_channels([channel])
        controller.write_configuration(chip,[52,53,54,55])
        controller.run(5, 'clear buffer')
        thresholds = []
        efficiencies = []
        for threshold in range(threshold_max, threshold_min-1, -threshold_step):
            # Set threshold and trim
            print('  threshold %d' % threshold)
            chip.config.global_threshold = threshold
            chip.config.reset_cycles = reset_cycles
            controller.write_configuration(chip,range(60,63)) # reset cycles
            controller.write_configuration(chip,[32,47]) # global threshold / xtrig
            controller.run(0.1, 'clear buffer')
            pulses_issued = 0
            triggers_received = 0
            dac_level = testpulse_dac_max
            print('  pulsing')
            for pulse_idx in range(n_pulses):
                if dac_level < (testpulse_dac_min + dac_pulse):
                    # Reset DAC level if it is too low to issue pulse
                    chip.config.csa_testpulse_dac_amplitude = testpulse_dac_max
                    controller.write_configuration(chip,46)
                    time.sleep(0.1) # Wait for front-end to settle
                    controller.run(0.1, 'clear buffer')
                    dac_level = testpulse_dac_max
                # Issue pulse
                dac_level -= dac_pulse  # Negative DAC step mimics electron arrival
                result = pulse_chip(controller, chip, dac_level)
                pulses_issued += 1
                triggers_received += len(result)
            print('  pulses issued: %d, triggers received: %d' % (pulses_issued, 
                                                                  triggers_received))
            efficiency = float(triggers_received)/pulses_issued
            thresholds.append(threshold)
            efficiencies.append(efficiency)
            if efficiency < min_acceptable_efficiency:
                continue
            else:
                if efficiency > max_acceptable_efficiency:
                    print('outside of max acceptable_efficiency')
                print('%d %d %d %d %.2f' % (channel, threshold, pulses_issued, 
                                            triggers_received, 
                                            float(triggers_received)/pulses_issued))
                results[channel] = {'thresholds' : thresholds,
                                    'efficiencies': efficiencies}
                break

    print('summary')
    print('  channel, lowest threshold reached, efficiency')
    for key in results:
        if isinstance(key, int):
            print('%d %d %.2f'% (key,results[key]['thresholds'][-1],
                                 results[key]['efficiencies'][-1]))

    if close_controller:
        controller.serial_close()
    return results

def scan_trim_with_pulse(controller=None, board='pcb-1', chip_idx=0,
                         channel_list=range(32), max_acceptable_efficiency=1.5,
                         min_acceptable_efficiency=0.5, n_pulses=100, dac_pulse=6,
                         testpulse_dac_max=235, testpulse_dac_min=229, reset_cycles=4096,
                         trim_max=31, trim_min=0, trim_step=1, threshold=40):
    ''' Pulse channels with test pulse to determine the minimum trim for
    triggering at least a specified efficiency '''
    close_controller = False
    if not controller:
        # Create controller and initialize chips to appropriate state
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
    chip = controller.chips[chip_idx]
    controller.run(5, 'clear buffer')
    results = {}
    for channel_idx, channel in enumerate(channel_list):
        print('configuring chip %d channel %d' % (chip.chip_id, channel))
        # Configure chip for pulsing one channel
        chip.config.csa_testpulse_enable = [1]*32 # Disconnect any channels
        chip.config.csa_testpulse_enable[channel] = 0 # Connect
        controller.write_configuration(chip,[42,43,44,45])
        # Initialize DAC level
        chip.config.csa_testpulse_dac_amplitude = testpulse_dac_max
        controller.write_configuration(chip,46)
        # Enable channel
        chip.config.disable_channels()
        chip.config.enable_channels([channel])
        controller.write_configuration(chip,[52,53,54,55])
        # Set threshold
        chip.config.global_threshold = threshold
        controller.write_configuration(chip,[32])
        controller.run(5, 'clear buffer')
        trims = []
        efficiencies = []
        for trim in range(trim_max, trim_min-1, -trim_step):
            # Set threshold and trim
            print('  trim %d' % trim)
            chip.config.pixel_trim_threshold = trim
            chip.config.reset_cycles = reset_cycles
            controller.write_configuration(chip,range(32)) # trim
            controller.write_configuration(chip,range(60,63)) # reset cycles
            controller.write_configuration(chip,[32,47]) # global threshold / xtrig
            controller.run(0.1, 'clear buffer')
            pulses_issued = 0
            triggers_received = 0
            dac_level = testpulse_dac_max
            print('  pulsing')
            for pulse_idx in range(n_pulses):
                if dac_level < (testpulse_dac_min + dac_pulse):
                    # Reset DAC level if it is too low to issue pulse
                    chip.config.csa_testpulse_dac_amplitude = testpulse_dac_max
                    controller.write_configuration(chip,46)
                    time.sleep(0.1) # Wait for front-end to settle
                    controller.run(0.1, 'clear buffer')
                    dac_level = testpulse_dac_max
                # Issue pulse
                dac_level -= dac_pulse  # Negative DAC step mimics electron arrival
                result = pulse_chip(controller, chip, dac_level)
                pulses_issued += 1
                triggers_received += len(result)
            print('  pulses issued: %d, triggers received: %d' % (pulses_issued, 
                                                                  triggers_received))
            efficiency = float(triggers_received)/pulses_issued
            trims.append(trim)
            efficiencies.append(efficiency)
            if efficiency < min_acceptable_efficiency and not trim == trim_min:
                continue
            else:
                if efficiency > max_acceptable_efficiency:
                    print('outside of max acceptable_efficiency')
                print('%d %d %d %d %.2f' % (channel, trim, pulses_issued, 
                                            triggers_received, 
                                            float(triggers_received)/pulses_issued))
                results[channel] = {'trims' : trims,
                                    'efficiencies': efficiencies}
                break

    print('summary')
    print('  channel, lowest trim reached, efficiency')
    for key in results:
        if isinstance(key, int):
            print('  %d %d %.2f' % (key, results[key]['trims'][-1],
                                    results[key]['efficiencies'][-1]))

    if close_controller:
        controller.serial_close()
    return results

def test_min_signal_amplitude(controller=None, board='pcb-1', chip_idx=0,
                              channel_list=range(32), threshold=40, trim=[16]*32,
                              threshold_trigger_rate=0.9, n_pulses=100, min_dac_amp=1,
                              max_dac_amp=20, dac_step=1, testpulse_dac_max=235,
                              testpulse_dac_min=40, reset_cycles=4096):
    ''' Pulse channel with increasing pulse sizes to determine the minimum pulse size for
    triggering at >90% '''
    close_controller = False
    if not controller:
        # Create controller and initialize chips to appropriate state
        close_controller = True
        controller = quickcontroller(board)
        disable_chips(controller)
    chip = controller.chips[chip_idx]
    results = {'channels_'}
    for channel_idx, channel in enumerate(channel_list):
        print('configuring for chip %d channel %d' % (chip.chip_id, channel))
        # Configure chip for pulsing one channel
        chip.config.csa_testpulse_enable[channel] = 0 # Connect
        controller.write_configuration(chip,[42,43,44,45])
        # Initialize DAC level, and issuing cross-triggers
        chip.config.csa_testpulse_dac_amplitude = testpulse_dac_max
        controller.write_configuration(chip,46)
        # Set threshold and trim
        chip.config.global_threshold = threshold
        chip.config.pixel_trim_thresholds[channel_idx] = trim[channel_idx]
        chip.config.reset_cycles = reset_cycles
        controller.write_configuration(chip,range(60,63)) # reset cycles
        controller.write_configuration(chip,range(32)) # trim
        controller.write_configuration(chip,[32,47]) # global threshold / xtrig
        for dac_amp in range(min_dac_amp, max_dac_amp+1, dac_step):
            # Step over a range of dac_amplitudes
            print('  pulse amp: %d' % dac_amp) 
            dac_level = max_dac_amp
            controller.run(0.1, 'clear buffer')
            del controller.reads[-1]
            pulses_issued = 0
            triggers_received = 0
            for pulse_idx in range(n_pulses):
                if dac_level < (testpulse_dac_min + pulse_dac):
                    # Reset DAC level if it is too low to issue pulse
                    chip.config.csa_testpulse_dac_amplitude = testpulse_dac_max
                    controller.write_configuration(chip,46)
                    time.sleep(reset_dac_time) # Wait for front-end to settle
                    controller.run(0.1, 'clear buffer')
                    del controller.reads[-1]
                    dac_level = testpulse_dac_max
                # Issue pulse
                dac_level -= dac_amp  # Negative DAC step mimics electron arrival
                result = pulse_chip(controller, chip, dac_level)
                pulses_issued += 1
                triggers_received += len(result)
            print('pulses issued: %d, triggers received: %d' % (pulses_issued, 
                                                                triggers_received))

    if close_controller:
        controller.serial_close()
    return

def analog_monitor(controller=None, board='pcb-5', chip_idx=0, channel=0):
    '''Connect analog monitor for this channel'''
    close_controller = False
    if not controller:
        # Create controller and initialize chips to appropriate state
        close_controller = True
        controller = quickcontroller(board)
    # Get chip under test
    chip = controller.chips[chip_idx]
    # Configure chip for analog monitoring
    chip.config.csa_monitor_select = [0,]*32
    chip.config.csa_monitor_select[channel] = 1
    controller.write_configuration(chip, [38,39,40,41])
    # return controller, for optional reuse
    if close_controller:
        controller.serial_close()
    return controller

def examine_global_scan(coarse_data, saturation_level=1000):
    '''Examine coarse threshold scan results, and determine optimum threshold'''
    result = {}
    sat_threshes = []
    chan_level_too_high = []
    chan_level_too_low = []
    for (channel_num, data) in coarse_data.iteritems():
        thresholds = data[0]
        npackets = data[1]
        adc_widths = data[3]
        saturation_thresh = -1
        saturation_npacket = -1
        # Only process if not already saturated
        if npackets[0] > saturation_level:
            chan_level_too_high.append(channel_num)
            continue
        if npackets[-1] <= saturation_level:
            chan_level_too_low.append(channel_num)
            continue
        for (thresh, npacket, adc_width) in zip(thresholds, npackets, adc_widths):
            if npacket > saturation_level:
                saturation_thresh = thresh
                saturation_npacket = npacket
                saturation_adc_width = adc_width
                sat_threshes.append(saturation_thresh)
                break
        result[channel_num] = {'saturation_thresh_global':saturation_thresh,
                               'saturation_npacket':saturation_npacket,
                               'saturation_adc_width':saturation_adc_width}
    # Collect other relevant results
    result['chan_level_too_high'] = chan_level_too_high
    result['chan_level_too_low'] = chan_level_too_low
    result['mean_thresh'] = sum(sat_threshes)/float(len(sat_threshes))
    return result

def examine_fine_scan(fine_data, saturation_level=1000):
    '''Examine coarse threshold scan results, and determine optimum threshold'''
    result = {}
    sat_trims = []
    chan_level_too_high = []
    chan_level_too_low = []
    for (channel_num, data) in fine_data.iteritems():
        trims = data[0]
        npackets = data[1]
        adc_widths = data[3]
        saturation_trim = -1
        saturation_npacket = -1
        # Only process if not already saturated
        if npackets[0] > saturation_level:
            chan_level_too_high.append(channel_num)
            continue
        if npackets[-1] <= saturation_level:
            chan_level_too_low.append(channel_num)
            continue
        for (trim, npacket, adc_width) in zip(trims, npackets, adc_widths):
            if npacket > saturation_level:
                saturation_trim = trim
                saturation_npacket = npacket
                saturation_adc_width = adc_width
                sat_trims.append(saturation_trim)
                break
        result[channel_num] = {'saturation_trim':saturation_trim,
                               'saturation_npacket':saturation_npacket,
                               'saturation_adc_width':saturation_adc_width}
    # Collect other relevant results
    result['chan_level_too_high'] = chan_level_too_high
    result['chan_level_too_low'] = chan_level_too_low
    return result

def run_threshold_test():
    # Run test
    cont = quickcontroller()
    disable_chips(cont)
    chip_results = []
    for chipidx in range(len(cont.chips)):
        print('%%%%%%%%%% Scanning chip: %d %%%%%%%%%%%%' % chipidx)
        chip_result = scan_threshold(controller=cont, chip_idx=chipidx)
        chip_results.append(chip_result)
    thresh_descs = []
    for chipidx in range(len(cont.chips)):
        thresh_desc = examine_global_scan(chip_results[chipidx])
        thresh_descs.append(thresh_desc)
    print('Mean Thresholds:')
    for chipidx in range(len(cont.chips)):
        ch_result = thresh_descs[chipidx]
        print('  Chip %d: %f' % (chipidx,ch_result['mean_thresh']))
    print('Out of range channels:')
    for chipidx in range(len(cont.chips)):
        ch_result = thresh_descs[chipidx]
        print('  Chip %d (high,low): %r, %r' % (
            chipidx,
            ch_result['chan_level_too_high'],
            ch_result['chan_level_too_low']))
    cont.serial_close()
    return (thresh_descs, chip_results)

def load_standard_test_configuration(path=None):
    if path is None:
        path = '.'
    with open(path + '/standard_test_configuration.json','r') as fi:
        test_config = json.load(fi)
    return test_config

def run_standard_tests(path=None):
    test_config = load_standard_test_configuration(path)
    results = {}
    for test in test_config:
        test_handle = None
        test_result = None
        if test['handle'] in globals():
            test_handle = globals()[test['handle']]
        if not test_handle is None:
            try:
                print('-'*10 + ' %s '% test['handle'] + '-'*10)
                print('%s(' % test['handle'])
                args = test['args']
                for arg in args:
                    print('    %s = %s,' % (arg, str(args[arg])))
                print('    )')

                test_result = test_handle(**args)
                results[test['handle']] = test_result

            except Exception as err:
                print('Failed!')
                print('Error: %s' % str(err))
                break_flag = ''
                while not break_flag in ['y','n','Y','N'] and not test is test_config[-1]:
                    print('Continue? (y/n)')
                    break_flag = raw_input()
                if break_flag is 'n':
                    break
            else:
                print('Done.')
    return results

if '__main__' == __name__:
    result1 = run_threshold_test()

    # result1 = scan_threshold()
    # result2 = noise_test_internal_pulser()
