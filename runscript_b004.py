#
# runscript.py
#


# ====================================================================================================

from __future__ import print_function

from mpi4py import MPI

import os
import sys
import errno
from copy import deepcopy
import time
import random
import math

import numpy as np
import pandas as pd
from shapely.geometry import Polygon, Point, shape, box
import shapefile


# ====================================================================================================
# general init


# mpi info
comm = MPI.COMM_WORLD
size = comm.Get_size()
rank = comm.Get_rank()
status = MPI.Status()

# absolute path to script directory
dir_base = os.path.dirname(os.path.abspath(__file__))

# python /path/to/runscript.py nepal NPL 0.1 10
arg = sys.argv

try:
    country = sys.argv[1]
    abbr = sys.argv[2]
    pixel_size = float(sys.argv[3])

    # maximum number of iterations to run
    # iterations = int(sys.argv[4])

    # raw_filter = sys.argv[5]

    # Ts = int(sys.argv[6])
    # Rid = int(sys.argv[7])
    Ts = int(time.time())
    Rid = str(Ts) +"_"+ "34567"

    # run_mean_surf = int(sys.argv[8])
    # run_mean_surf = 3
    # path_mean_surf = "chains/nepal/nepal_0.5_1432844232_12347/outputs/output_nepal_0.5_surf.npy"

    # if run_mean_surf == 3:
        # path_mean_surf = sys.argv[9]


    # data_version = sys.argv[10]
    # run_version = sys.argv[11]
    data_version = 1.1
    run_version = "b005"

    # log_mean_surf = int(sys.argv[12])
    # log_mean_surf = 0


except:
    sys.exit("invalid inputs")


# maximum number of iterations to run
iter_max = 1000
# iterations = 100

# iterations range
i_control = range(int(iter_max))

# iteration intervals at which to check error val
iter_interval = [10, 50, 100, 250, 500, 750, 1000, 5000, 10000, 50000, 100000]

# alternative to intervals, fixed steps in iterations at which to check error
iter_step = 0

# difference from true mean (decimal percentage)
iter_thresh = 0.05

# minimum improvement over previous iteration interval required to continue (decimal percentage)
iter_improvement = 0.01


# check for valid pixel size
# examples of valid pixel sizes: 1.0, 0.5, 0.25, 0.2, 0.1, 0.05, 0.025, ...
if (1/pixel_size) != int(1/pixel_size):
    sys.exit("invalid pixel size: "+str(pixel_size))

# pixel size inverse
psi = 1/pixel_size


# --------------------------------------------------
# filter options


# filter_type = "all"
filter_type = "specfic"

filters = {
    "ad_sector_names": {
        "Agriculture": 0
    }
}


# --------------------------------------------------
# vars to be added as inputs
# not used by mcr function

# nodata value for output raster
nodata = -9999

# field name for aid values
aid_field = "total_commitments"

# boolean field identifying if project is geocoded
is_geocoded = "is_geocoded"

# when True, only use geocoded data
only_geocoded = False


# --------------------------------------------------
# static vars that may be added as some type of input
# used by mcr functions

# type definition for non geocoded projects
# either allocated at country level ("country") or ignored ("None")
not_geocoded = "country"

if only_geocoded:
    not_geocoded = "None"


# fields name associated with values in lookup dict
code_field = "precision_code"

# aggregation types used in lookup dict
agg_types = ["point", "buffer", "adm"]

# code field values
lookup = {
    "1": {"type":"point","data":0},
    "2": {"type":"buffer","data":1},
    "3": {"type":"adm","data":"2"},
    "4": {"type":"adm","data":"2"},
    "5": {"type":"buffer","data":1},
    "6": {"type":"adm","data":"0"},
    "7": {"type":"adm","data":"0"},
    "8": {"type":"adm","data":"0"}
}


# ====================================================================================================
# functions


# check csv delim and return if valid type
def getCSV(path):
    if path.endswith('.tsv'):
        return pd.read_csv(path, sep='\t', quotechar='\"', na_values='', keep_default_na=False)
    elif path.endswith('.csv'):
        return pd.read_csv(path, quotechar='\"', na_values='', keep_default_na=False)
    else:
        sys.exit('getCSV - file extension not recognized.\n')


# get project and location data in path directory
# requires a field name to merge on and list of required fields
def getData(path, merge_id, field_ids, only_geo):

    amp_path = path+"/projects.tsv"
    loc_path = path+"/locations.tsv"

    # make sure files exist
    #

    # read input csv files into memory
    amp = getCSV(amp_path)
    loc = getCSV(loc_path)


    if not merge_id in amp or not merge_id in loc:
        sys.exit("getData - merge field not found in amp or loc files")

    amp[merge_id] = amp[merge_id].astype(str)
    loc[merge_id] = loc[merge_id].astype(str)

    # create projectdata by merging amp and location files by project_id
    if only_geo:
        tmp_merged = amp.merge(loc, on=merge_id)
    else:
        tmp_merged = amp.merge(loc, on=merge_id, how="left")

    if not "longitude" in tmp_merged or not "latitude" in tmp_merged:
        sys.exit("getData - latitude and longitude fields not found")

    for field_id in field_ids:
        if not field_id in tmp_merged:
            sys.exit("getData - required code field not found")

    return tmp_merged


# --------------------------------------------------


# defin tags enum
def enum(*sequential, **named):
    # source: http://stackoverflow.com/questions/36932/how-can-i-represent-an-enum-in-python
    enums = dict(zip(sequential, range(len(sequential))), **named)
    return type('Enum', (), enums)


# gets geometry type based on lookup table
# depends on lookup and not_geocoded
def geomType(is_geo, code):

    try:
        is_geo = int(is_geo)
        code = str(int(code))

        if is_geo == 1:
            if code in lookup:
                tmp_type = lookup[code]["type"]
                return tmp_type

            else:
                print("lookup code not recognized: " + code)
                return "None"

        elif is_geo == 0:
            return not_geocoded

        else:
            print("is_geocoded integer code not recognized: " + str(is_geo))
            return "None"

    except:
        return not_geocoded



# finds shape in set of polygons which arbitrary polygon is within
# returns 0 if item is not within any of the shapes
def getPolyWithin(item, polys):
    c = 0
    for shp in polys:
        tmp_shp = shape(shp)
        if item.within(tmp_shp):
            return tmp_shp

    return c


# checks if arbitrary polygon is within country (adm0) polygon
# depends on adm0
def inCountry(shp):
    return shp.within(adm0)


# build geometry for point based on code
# depends on lookup and adm0
def getGeom(code, lon, lat):
    tmp_pnt = Point(lon, lat)

    if not inCountry(tmp_pnt):
        print("point not in country")
        return 0

    elif lookup[code]["type"] == "point":
        return tmp_pnt

    elif lookup[code]["type"] == "buffer":
        try:
            tmp_int = float(lookup[code]["data"])
            tmp_buffer = tmp_pnt.buffer(tmp_int)

            if inCountry(tmp_buffer):
                return tmp_buffer
            else:
                return tmp_buffer.intersection(adm0)

        except:
            print("buffer value could not be converted to float")
            return 0

    elif lookup[code]["type"] == "adm":
        try:
            tmp_int = int(lookup[code]["data"])
            return getPolyWithin(tmp_pnt, adm_shps[tmp_int])

        except:
            print("adm value could not be converted to int")
            return 0

    else:
        print("code type not recognized")
        return 0


# returns geometry for point
# depends on agg_types and adm0
def geomVal(agg_type, code, lon, lat):
    if agg_type in agg_types:

        code = str(int(code))
        tmp_geom = getGeom(code, lon, lat)

        if tmp_geom != 0:
            return tmp_geom

        return "None"

    elif agg_type == "country":

        return adm0

    else:
        print("agg_type not recognized: " + str(agg_type))
        return "None"


# --------------------------------------------------


# random point gen function
def get_random_point_in_polygon(poly):

    INVALID_X = -9999
    INVALID_Y = -9999

    (minx, miny, maxx, maxy) = poly.bounds
    p = Point(INVALID_X, INVALID_Y)
    px = 0
    while not poly.contains(p):
        p_x = random.uniform(minx, maxx)
        p_y = random.uniform(miny, maxy)
        p = Point(p_x, p_y)
    return p


# generate random point geom or use actual point
def addPt(agg_type, agg_geom):
    if agg_type == "point":
        return agg_geom
    else:
        tmp_rnd = get_random_point_in_polygon(agg_geom)
        return tmp_rnd


# ====================================================================================================


# --------------------------------------------------
# load shapefiles

# must start at and inlcude ADM0
# all additional ADM shps must be included so that adm_path index corresponds to adm level
adm_paths = []
adm_paths.append(dir_base+"/countries/"+country+"/shapefiles/ADM0/"+abbr+"_adm0.shp")
adm_paths.append(dir_base+"/countries/"+country+"/shapefiles/ADM1/"+abbr+"_adm1.shp")
adm_paths.append(dir_base+"/countries/"+country+"/shapefiles/ADM2/"+abbr+"_adm2.shp")

# get adm0 bounding box
adm_shps = [shapefile.Reader(adm_path).shapes() for adm_path in adm_paths]

# define country shape
adm0 = shape(adm_shps[0][0])


# --------------------------------------------------
# create point grid for country

# country bounding box
(adm0_minx, adm0_miny, adm0_maxx, adm0_maxy) = adm0.bounds
# print( (adm0_minx, adm0_miny, adm0_maxx, adm0_maxy) )

# grid_buffer
gb = 0.5

# bounding box rounded to pixel size (always increases bounding box size, never decreases)
(adm0_minx, adm0_miny, adm0_maxx, adm0_maxy) = (math.floor(adm0_minx*gb)/gb, math.floor(adm0_miny*gb)/gb, math.ceil(adm0_maxx*gb)/gb, math.ceil(adm0_maxy*gb)/gb)
# print( (adm0_minx, adm0_miny, adm0_maxx, adm0_maxy) )

# generate arrays of new grid x and y values
cols = np.arange(adm0_minx, adm0_maxx+pixel_size*0.5, pixel_size)
rows = np.arange(adm0_maxy, adm0_miny-pixel_size*0.5, -1*pixel_size)

# print cols
# print rows

# init grid reference object
gref = {}
idx = 0
for r in rows:
    gref[str(r)] = {}
    for c in cols:
        # build grid reference object
        gref[str(r)][str(c)] = idx
        idx += 1


# --------------------------------------------------
# load project data

dir_data = dir_base+"/countries/"+country+"/data/"+country+"_"+str(data_version)+"/data"

merged = getData(dir_data, "project_id", (code_field, "project_location_id"), only_geocoded)


# --------------------------------------------------
# misc data prep

# create copy of merged project data
# i_m = deepcopy(merged)

# get location count for each project
merged['ones'] = (pd.Series(np.ones(len(merged)))).values

# get project location count
grouped_location_count = merged.groupby('project_id')['ones'].sum()


# create new empty dataframe
df_location_count = pd.DataFrame()

# add location count series to dataframe
df_location_count['location_count'] = grouped_location_count

# add project_id field
df_location_count['project_id'] = df_location_count.index

# merge location count back into data
merged = merged.merge(df_location_count, on='project_id')

# aid field value split evenly across all project locations based on location count
merged[aid_field].fillna(0, inplace=True)
merged['split_dollars_pp'] = (merged[aid_field] / merged.location_count)


# --------------------------------------------------
# filters

# apply filters to project data
# filtered = merged.loc[merged.ad_sector_names == "Agriculture"]

# !!! potential issue !!!
#
# - filters which remove only some locations from a project will skew aid splits
# - moved original project location count to before filters so that it can be used to
#   compare the count of project locations before filter to count after and generate
#   placeholder random values for the locations that were filtered out
# - method: recheck project location count and create placeholder random value if locations are missing
# - will need to rebuild how random num column is added. probaby can use apply with a new function

filtered = deepcopy(merged)

# --------------------------------------------------
# assign geometries

# add geom columns
filtered["agg_type"] = ["None"] * len(filtered)
filtered["agg_geom"] = ["None"] * len(filtered)

filtered.agg_type = filtered.apply(lambda x: geomType(x[is_geocoded], x[code_field]), axis=1)
filtered.agg_geom = filtered.apply(lambda x: geomVal(x.agg_type, x[code_field], x.longitude, x.latitude), axis=1)

i_m = filtered.loc[filtered.agg_geom != "None"].copy(deep=True)


# i_m['index'] = i_m['project_location_id']
i_m['unique'] = range(0, len(i_m))
i_m['index'] = range(0, len(i_m))
i_m = i_m.set_index('index')


# ====================================================================================================
# master init


if rank == 0:

    # --------------------------------------------------
    # initialize results file output

    results_str = "Monte Carlo Rasterization Output File\t "

    results_str += "\nstart time\t" + str(Ts)
    results_str += "\ncountry\t" + str(country)
    results_str += "\nabbr\t" + str(abbr)
    results_str += "\npixel_size\t" + str(pixel_size)
    results_str += "\niter_max\t" + str(iter_max)
    results_str += "\nnodata\t" + str(nodata)
    results_str += "\naid_field\t" + str(aid_field)
    results_str += "\ncode_field\t" + str(code_field)
    results_str += "\ncountry bounds\t" + str((adm0_minx, adm0_miny, adm0_maxx, adm0_maxy))

    results_str += "\nrows\t" + str(len(rows))
    results_str += "\ncolumns\t" + str(len(cols))
    results_str += "\nlocations\t" + str(len(i_m))

    # results_str += "\nfilters\t" + str(filters)


    # --------------------------------------------------
    # initialize asc file output

    asc = ""
    asc += "NCOLS " + str(len(cols)) + "\n"
    asc += "NROWS " + str(len(rows)) + "\n"

    # asc += "XLLCORNER " + str(adm0_minx-pixel_size*0.5) + "\n"
    # asc += "YLLCORNER " + str(adm0_miny-pixel_size*0.5) + "\n"

    asc += "XLLCENTER " + str(adm0_minx) + "\n"
    asc += "YLLCENTER " + str(adm0_miny) + "\n"

    asc += "CELLSIZE " + str(pixel_size) + "\n"
    asc += "NODATA_VALUE " + str(nodata) + "\n"

    # --------------------------------------------------
    # build output directories

    # creates directories
    def make_dir(path):
        try:
            os.makedirs(path)
        except OSError as exception:
            if exception.errno != errno.EEXIST:
                raise


    # dir_country = dir_base+"/outputs/"+country
    # dir_working = dir_country+"/"+country+"_"+str(pixel_size)+"_"+str(iterations)+"_"+str(int(Ts))


    dir_country = dir_base+"/chains/"+country
    dir_chain = dir_country+"/"+country+"_"+str(data_version)+"_"+run_version+"_"+str(pixel_size)
    dir_outputs = dir_chain+"/outputs"
    dir_working = dir_outputs+"/"+str(Rid)


    make_dir(dir_working)


    # --------------------------------------------------
    # record init runtime

    T_init = time.time()
    Tloc = int(T_init - Ts)

    results_str += "\nInit Runtime\t" + str(Tloc//60) +'m '+ str(int(Tloc%60)) +'s'
    print('\tInit Runtime: ' + str(Tloc//60) +'m '+ str(int(Tloc%60)) +'s')



# ====================================================================================================
# ====================================================================================================

comm.Barrier()
# sys.exit("! - init only")

# ====================================================================================================
# ====================================================================================================
# mpi prep


# terminate if master init fails
#

# Define MPI message tags
tags = enum('READY', 'DONE', 'EXIT', 'START', 'ERROR')

# init for later
sum_mean_surf = 0

# check if mean surf exists
load_mean_surf = dir_outputs+"/mean_surf.npy"
run_mean_surf = 1
if os.path.isfile(load_mean_surf) :
    run_mean_surf = 0


# ====================================================================================================
# generate mean surface raster


if run_mean_surf == 0 and rank == 0:
    sum_mean_surf = np.load(load_mean_surf)

elif run_mean_surf == 1 and rank == 0:

    # ==================================================
    # MASTER START STUFF


    all_mean_surf = []
    unique_ids = i_m['unique']

    # ==================================================

    task_index = 0
    num_workers = size - 1
    closed_workers = 0
    err_status = 0
    print("Mean Surf Master starting with %d workers" % num_workers)

    # distribute work
    while closed_workers < num_workers:
        data = comm.recv(source=MPI.ANY_SOURCE, tag=MPI.ANY_TAG, status=status)
        source = status.Get_source()
        tag = status.Get_tag()

        if tag == tags.READY:
            # Worker is ready, so send it a task
            if task_index < len(unique_ids):

                #
                # !!!
                #   if task if for a point (not point with small buffer, etc.)
                #   then let master do work
                #   run tests to see if this actually improves runtimes
                # !!!
                #

                comm.send(unique_ids[task_index], dest=source, tag=tags.START)
                print("Sending surf task %d to worker %d" % (task_index, source))
                task_index += 1
            else:
                comm.send(None, dest=source, tag=tags.EXIT)

        elif tag == tags.DONE:

            # ==================================================
            # MASTER MID STUFF


            all_mean_surf.append(data)
            print("Got surf data from worker %d" % source)


            # ==================================================

        elif tag == tags.EXIT:
            print("Surf orker %d exited." % source)
            closed_workers += 1

        elif tag == tags.ERROR:
            print("Error reported by surf worker %d ." % source)
            # broadcast error to all workers
            #
            # make sure they all get message and terminate
            #
            err_status = 1
            break

    # ==================================================
    # MASTER END STUFF


    if err_status == 0:
        # calc results
        print("Mean Surf Master calcing")

        stack_mean_surf = np.vstack(all_mean_surf)
        sum_mean_surf = np.sum(stack_mean_surf, axis=0)

        save_mean_surf = dir_outputs+"/mean_surf.npy"
        np.save(save_mean_surf, sum_mean_surf)

        # write asc file

        sum_mean_surf_str = ' '.join(np.char.mod('%f', sum_mean_surf))
        asc_sum_mean_surf_str = asc + sum_mean_surf_str

        fout_sum_mean_surf = open(dir_outputs+"/mean_surf.asc", "w")
        fout_sum_mean_surf.write(asc_sum_mean_surf_str)


        print("Mean Surf Master finishing")


    else:
        print("Mean Surf Master terminating due to worker error.")


    # ==================================================


elif run_mean_surf == 1:
    # Worker processes execute code below
    name = MPI.Get_processor_name()
    print("Surf worker rank %d on %s." % (rank, name))
    while True:
        comm.send(None, dest=0, tag=tags.READY)
        task = comm.recv(source=0, tag=MPI.ANY_TAG, status=status)
        tag = status.Get_tag()

        if tag == tags.START:

            # ==================================================
            # WORKER STUFF

            mean_surf = np.zeros((int(idx+1),), dtype=np.int)

            # poly grid pixel size and poly grid pixel size inverse
            # poly grid pixel size is 1 order of magnitude higher resolution than output pixel_size
            pg_pixel_size = pixel_size * 0.1
            pg_psi = 1/pg_pixel_size


            pg_data = i_m.loc[task]
            pg_type = pg_data.agg_type


            if (pg_type != "point" and pg_type in agg_types) or pg_type == "country":

                # for each row generate grid based on bounding box of geometry

                pg_geom = pg_data.agg_geom

                (pg_minx, pg_miny, pg_maxx, pg_maxy) = pg_geom.bounds
                # print( (pg_minx, pg_miny, pg_maxx, pg_maxy) )

                (pg_minx, pg_miny, pg_maxx, pg_maxy) = (math.floor(pg_minx*pg_psi)/pg_psi, math.floor(pg_miny*pg_psi)/pg_psi, math.ceil(pg_maxx*pg_psi)/pg_psi, math.ceil(pg_maxy*pg_psi)/pg_psi)
                # print( (pg_minx, pg_miny, pg_maxx, pg_maxy) )

                pg_cols = np.arange(pg_minx, pg_maxx+pg_pixel_size*0.5, pg_pixel_size)
                pg_rows = np.arange(pg_maxy, pg_miny-pg_pixel_size*0.5, -1*pg_pixel_size)

                # evenly split the aid for that row (i_m['split_dollars_pp'] field) among new grid points

                # full poly grid reference object and count
                pg_gref = {}
                pg_idx = 0

                # poly grid points within actual geom and count
                # pg_in = {}
                pg_count = 0

                for r in pg_rows:
                    pg_gref[str(r)] = {}

                    for c in pg_cols:
                        pg_idx += 1

                        # check if point is within geom
                        pg_point = Point(c,r)
                        pg_within = pg_point.within(pg_geom)

                        if pg_within:
                            pg_gref[str(r)][str(c)] = pg_idx
                            pg_count += 1
                        else:
                            pg_gref[str(r)][str(c)] = "None"


                # init grid reference object
                for r in pg_rows:
                    for c in pg_cols:
                        if pg_gref[str(r)][str(c)] != "None":
                            # round new grid points to old grid points and update old grid
                            gref_id = gref[str(round(r * psi) / psi)][str(round(c * psi) / psi)]
                            mean_surf[gref_id] += pg_data['split_dollars_pp'] / pg_count


            elif pg_type == "point":

                # round new grid points to old grid points and update old grid
                gref_id = gref[str(round(pg_data.latitude * psi) / psi)][str(round(pg_data.longitude * psi) / psi)]
                mean_surf[gref_id] += pg_data['split_dollars_pp']


            # --------------------------------------------------
            # send np arrays back to master

            comm.send(mean_surf, dest=0, tag=tags.DONE)


            # ==================================================

        elif tag == tags.EXIT:
            comm.send(None, dest=0, tag=tags.EXIT)
            break

        elif tag == tags.ERROR:
            print("Error message from surf master. Shutting down." % source)
            # confirm error message received
            #
            # terminate process
            #
            break


# elif run_mean_surf == 2 and rank == 0:
#     load_mean_surf = dir_base+"/surf_log/"+country+"_"+str(data_version)+"_"+str(run_version)+"_"+str(pixel_size)+".npy"
#     sum_mean_surf = np.load(load_mean_surf)

# elif run_mean_surf == 3 and rank == 0:
#     load_mean_surf = dir_base+"/"+path_mean_surf
#     sum_mean_surf = np.load(load_mean_surf)


# if log_mean_surf == 1 and rank == 0:
#     save_mean_surf = dir_base+"/surf_log/"+country+"_"+str(data_version)+"_"+str(run_version)+"_"+str(pixel_size)+".npy"
#     np.save(save_mean_surf, sum_mean_surf)


if rank == 0:

    T_surf = time.time()
    Tloc = int(T_surf - T_init)

    results_str += "\nMean Surf Runtime\t" + str(Tloc//60) +'m '+ str(int(Tloc%60)) +'s'
    results_str += "\nMean Surf Command\t" + str(run_mean_surf)

    print('\t\tMean Surf Runtime: ' + str(Tloc//60) +'m '+ str(int(Tloc%60)) +'s')
    print('\t\tMean Surf Command: ' + str(run_mean_surf))

    print('\n')


# ====================================================================================================
# ====================================================================================================

comm.Barrier()
# sys.exit("! - mean surf only")

# ====================================================================================================
# ====================================================================================================
# mpi stuff
# structured based on https://github.com/jbornschein/mpi4py-examples/blob/master/09-task-pull.py


if rank == 0:

    # ==================================================
    # MASTER START STUFF

    total_aid = []
    total_count = []

    task_index = 0
    num_workers = size - 1
    closed_workers = 0
    err_status = 0

    print("Iter master starting with %d workers" % (num_workers))

    # ==================================================


    # distribute work
    while closed_workers < num_workers:
        data = comm.recv(source=MPI.ANY_SOURCE, tag=MPI.ANY_TAG, status=status)
        source = status.Get_source()
        tag = status.Get_tag()

        if tag == tags.READY:


            # check error value at intervals
            this_interval = len(total_aid)

            if this_interval in iter_interval:

                # check error percent value
                this_stack_aid = np.vstack(total_aid[0:this_interval])
                this_mean_aid = np.mean(this_stack_aid, axis=0)

                this_sum_aid = np.sum(this_mean_aid)

                this_error_surf = np.absolute(np.subtract(sum_mean_surf, this_mean_aid))

                this_error_log_sum = np.sum(np.absolute(this_error_surf))
                this_error_log_percent =  this_error_log_sum / this_sum_aid

                # determine if threshold is met
                if this_error_log_percent < iter_thresh:
                    # end if threshold is met
                    print("Iter thresh met at %d iterations" % this_interval)
                    iterations = this_interval

                    for i in range(1, size):
                        comm.send(None, dest=i, tag=tags.EXIT)

                    break

                else:
                    # keep going if threshold not met
                    print("Iter thresh not met at %d iterations" % this_interval)


            if task_index < len(i_control):
                comm.send(i_control[task_index], dest=source, tag=tags.START)
                print("Sending iter task %d to worker %d" % (task_index, source))
                task_index += 1

            else:
                iterations = task_index
                comm.send(None, dest=source, tag=tags.EXIT)

        elif tag == tags.DONE:

            # ==================================================
            # MASTER MID STUFF

            total_aid.append(data[0])
            total_count.append(data[1])
            print("Got iter data from worker %d" % source)

            # ==================================================

        elif tag == tags.EXIT:
            print("Iter worker %d exited." % source)
            closed_workers += 1

        elif tag == tags.ERROR:
            print("Error reported by iter worker %d ." % source)
            # broadcast error to all workers
            #
            # make sure they all get message and terminate
            #
            err_status = 1
            break

    # ==================================================
    # MASTER END STUFF

    if err_status == 0:
        # calc results
        print("Iter master processing results")

        stack_aid = np.vstack(total_aid)
        std_aid = np.std(stack_aid, axis=0)
        mean_aid = np.mean(stack_aid, axis=0)

        sum_aid = np.sum(mean_aid)

        stack_count = np.vstack(total_count)
        std_count = np.std(stack_count, axis=0)
        mean_count = np.mean(stack_count, axis=0)


        # error_log = 0
        if type(sum_mean_surf) != type(0):
            error_surf = np.absolute(np.subtract(sum_mean_surf, mean_aid))

            error_surf_str = ' '.join(np.char.mod('%f', error_surf))
            asc_error_surf_str = asc + error_surf_str

            fout_error_surf = open(dir_working+"/error_surf.asc", "w")
            fout_error_surf.write(asc_error_surf_str)

            error_log_mean = np.mean(np.absolute(error_surf))
            error_log_sum = np.sum(np.absolute(error_surf))
            error_log_percent =  error_log_sum / sum_aid

            results_str += "\nerror mean\t" + str(error_log_mean)
            results_str += "\nerror sum\t" + str(error_log_sum)
            results_str += "\nerror percent\t" + str(error_log_percent)


        # write core asc output files

        std_aid_str = ' '.join(np.char.mod('%f', std_aid))
        asc_std_aid_str = asc + std_aid_str
        fout_std_aid = open(dir_working+"/std_aid.asc", "w")
        fout_std_aid.write(asc_std_aid_str)

        mean_aid_str = ' '.join(np.char.mod('%f', mean_aid))
        asc_mean_aid_str = asc + mean_aid_str
        fout_mean_aid = open(dir_working+"/mean_aid.asc", "w")
        fout_mean_aid.write(asc_mean_aid_str)

        std_count_str = ' '.join(np.char.mod('%f', std_count))
        asc_std_count_str = asc + std_count_str
        fout_std_count = open(dir_working+"/std_count.asc", "w")
        fout_std_count.write(asc_std_count_str)

        mean_count_str = ' '.join(np.char.mod('%f', mean_count))
        asc_mean_count_str = asc + mean_count_str
        fout_mean_count = open(dir_working+"/mean_count.asc", "w")
        fout_mean_count.write(asc_mean_count_str)


        # calc section runtime and total runtime
        T_iter = int(time.time() - T_surf)
        Tloc = int(time.time() - Ts)

        # print final results
        print('\n\tRun Results:')
        print('\t\tError Value for ' + str(iterations) + ' iterations: ' + str(error_log_percent))
        print('\t\tIterations Runtime: ' + str(T_iter//60) +'m '+ str(int(T_iter%60)) +'s')
        print('\t\tTotal Runtime: ' + str(Tloc//60) +'m '+ str(int(Tloc%60)) +'s')
        print('\n\n')

        # write to results.tsv
        results_str += "\nIterations Runtime\t" + str(T_iter//60) +'m '+ str(int(T_iter%60)) +'s'
        results_str += "\nTotal Runtime\t" + str(Tloc//60) +'m '+ str(int(Tloc%60)) +'s'

        fout_results = open(dir_working+"/results.tsv", "w")
        fout_results.write(results_str)


        # write to main log
        fout_log = open(dir_base+"/outputs/"+"run_log.tsv", "a")
        # log : id, start, country, abbr, pixel_size, max_iterations, actual_iterations, percent_error, geocoded_only, processes, runtime
        fout_array = [Rid, Ts, country, abbr, pixel_size, iter_max, iterations, error_log_percent, only_geocoded, size, Tloc]
        fout_str = "\t".join(str(x) for x in fout_array)
        fout_log.write(fout_str + "\n")


    else:
        print("Iter master terminating due to worker error.")


    # ==================================================


else:
    # Worker processes execute code below
    name = MPI.Get_processor_name()
    print("Iter worker rank %d on %s." % (rank, name))
    while True:
        comm.send(None, dest=0, tag=tags.READY)
        task = comm.recv(source=0, tag=MPI.ANY_TAG, status=status)
        tag = status.Get_tag()

        if tag == tags.START:

            # ==================================================
            # WORKER STUFF


            # --------------------------------------------------
            # generate random dollars

            i_mx = deepcopy(i_m)


            # add new column of random numbers (0-1)
            i_mx['ran_num'] = (pd.Series(np.random.random(len(i_mx)))).values

            # group merged table by project ID for the sum of each project ID's random numbers
            grouped_random_series = i_mx.groupby('project_id')['ran_num'].sum()


            # create new empty dataframe
            df_group_random = pd.DataFrame()

            # add grouped random 'Series' to the newly created 'Dataframe' under a new grouped_random column
            df_group_random['grouped_random'] = grouped_random_series

            # add the series index, composed of project IDs, as a new column called project_id
            df_group_random['project_id'] = df_group_random.index


            # now that we have project_id in both the original merged 'Dataframe' and the new 'Dataframe' they can be merged
            i_mx = i_mx.merge(df_group_random, on='project_id')

            # calculate the random dollar ammount per point for each entry
            i_mx['random_dollars_pp'] = (i_mx.ran_num / i_mx.grouped_random) * i_mx[aid_field]


            # --------------------------------------------------
            # assign random points

            # add random points column to table
            i_mx["rnd_pt"] = [0] * len(i_mx)
            i_mx.rnd_pt = i_mx.apply(lambda x: addPt(x.agg_type, x.agg_geom), axis=1)

            # drop rnd_x and rnd_y if they exist
            if "rnd_x" in i_mx.columns or "rnd_y" in i_mx.columns:
                i_mx.drop(['rnd_x','rnd_y'], inplace=True, axis=1)

            # round rnd_pts to match point grid
            i_mx = i_mx.merge(i_mx.rnd_pt.apply(lambda s: pd.Series({'rnd_x':(round(s.x * psi) / psi), 'rnd_y':(round(s.y * psi) / psi)})), left_index=True, right_index=True)


            # --------------------------------------------------
            # add results to output arrays

            # initialize mean and count grids with zeros
            npa_aid = np.zeros((int(idx+1),), dtype=np.int)
            npa_count = np.zeros((int(idx+1),), dtype=np.int)

            # add commitment value for each rnd pt to grid value
            for i in i_mx.iterrows():
                try:
                    nx = str(i[1].rnd_x)
                    ny = str(i[1].rnd_y)
                    # print nx, ny, gref[nx][ny]
                    if int(i[1].random_dollars_pp) > 0:
                        npa_aid[gref[ny][nx]] += int(i[1].random_dollars_pp)
                        npa_count[gref[ny][nx]] += int(1)
                except:
                    print("Error on iter worker %d with tasks %s." % (rank, task))


            # --------------------------------------------------
            # send np arrays back to master

            npa_result = np.array([npa_aid,npa_count])
            comm.send(npa_result, dest=0, tag=tags.DONE)


            # ==================================================

        elif tag == tags.EXIT:
            comm.send(None, dest=0, tag=tags.EXIT)
            break

        elif tag == tags.ERROR:
            print("Error message from iter master. Shutting down." % source)
            # confirm error message received
            #
            # terminate process
            #
            break


