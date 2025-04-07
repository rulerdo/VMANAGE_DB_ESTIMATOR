import csv
import urllib3
import os
import math
from catalystwan.session import create_manager_session
import gradio as gr

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_stats_db_configuration(session):

    stats_db_config_endpoint = "/dataservice/management/elasticsearch/index/size"
    response = session.get(stats_db_config_endpoint)
    stats_db_config = response.json()

    return stats_db_config


def get_estimate_from_live_vmanage(session):

    estimate_db_endpoint = "/dataservice/management/elasticsearch/index/size/estimate"
    response = session.get(estimate_db_endpoint)
    data = response.json()
    raw_indexes = data[1]["Per index disk space "]
    indexes = [row for row in raw_indexes if row.get("status") != "fail"]

    return indexes


def parse_headers_from_indexes(indexes):

    headers = (
        ["Index", "Allocated Space", "Used Space"]
        + list(indexes[0]["dataSetInfo"].keys())
        + [f"Estimation for: {key}" for key in indexes[0]["estimation"].keys()]
    )

    return headers


def parse_data_table(indexes, filtered_stats_config):

    data_table = []

    for config in filtered_stats_config:
        index_from_config = config[0]

        for i in indexes:
            if i.get("index") == index_from_config:
                row = (
                    config
                    + list(i["dataSetInfo"].values())
                    + list(i["estimation"].values())
                )

        data_table.append(row)

    return data_table


def save_data_to_csv(destination_file, headers, data):

    with open(destination_file, "w") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(headers)
        csv_writer.writerows(data)

    gr.Info(f"\n{destination_file} saved!")

    return destination_file


def filter_config_from_indexes(indexes, stats_db_config):

    filtered_stats_config = []
    for i in indexes:
        for index in stats_db_config["indexSize"]:
            if i["index"] == index["displayName"]:
                filtered_stats_config.append(
                    [
                        index["displayName"],
                        index["sizeInGB"],
                        index["currentSize"].upper(),
                    ]
                )

    return filtered_stats_config


def parse_unused_available_space(stats_db_config):

    unused_indexes = []
    unused_allocated_space = 0
    unused_indexes_message = "No unused indexes found"

    for index in stats_db_config["indexSize"]:
        if index["currentSize"] == "0gb":
            unused_indexes.append(index["displayName"])
            unused_allocated_space += index["sizeInGB"]

    if unused_indexes:
        unused_indexes_message = f"{', '.join(unused_indexes)}"
        unused_indexes_message += "\n\n"
        unused_indexes_message += f"Unused Indexes count: {len(unused_indexes)}"
        unused_indexes_message += "\n"
        unused_indexes_message += (
            f"Space allocated to Unused Indexes: {unused_allocated_space}GB"
        )

    return unused_indexes_message


def create_current_state_message(stats_db_config):

    current_state_message = ""
    current_state_message += f"Total space: {stats_db_config['availableSpaceInGB']}GB\n"
    current_state_message += (
        f"Used space: {stats_db_config['elasticUsedSpaceInGB']}GB\n"
    )
    current_state_message += f"Available space: {stats_db_config['availableSpaceInGB'] - stats_db_config['elasticUsedSpaceInGB']}GB"

    return current_state_message


def create_recommendation(min_days, stats_db_config, data_table):

    day_to_table_map = {1: 8, 7: 9, 14: 10, 30: 11, 90: 12, 180: 13, 365: 14}

    if not min_days:
        min_days = 7
    else:

        if min_days not in day_to_table_map.keys():
            invalid_day_value_message = "*** Recommendation failure ***"
            invalid_day_value_message += f"\nInvalid value for Min days: {min_days}"
            invalid_day_value_message += f"\nPlease use one of the available options: {list(day_to_table_map.keys())}"
            gr.Warning(f"Invalid value for Min days: {min_days}")
            return invalid_day_value_message

        int(min_days)

    recommendation = []

    required_space = 0
    for line in data_table:
        increment = 0
        if line[4] < min_days:
            increment = math.ceil(
                float(line[day_to_table_map[min_days]][:-3]) - line[1]
            )
            if line[day_to_table_map[min_days]][-2:] == "TB":
                increment = (
                    float(line[day_to_table_map[min_days]][:-3]) * 1000 - line[1]
                )
            display_name = line[0]
            if increment > 0:
                recommendation.append(
                    f"{display_name}: Increase {(increment)}GB (Set to {line[1] + increment}GB)"
                )
                required_space += increment

    available_space = stats_db_config["availableSpaceInGB"]

    if required_space > 0:

        recommendation.append("")

        enough_space = required_space < available_space
        recommendation.append(
            f"Total required space for {min_days} days of storage data: {math.ceil(required_space)} GB"
        )

        if enough_space:
            recommendation.append(
                f"Space available after allocation: {available_space - math.ceil(required_space)} GB"
            )

        else:
            recommendation.append(
                "*** Not enough available space to allocate requirement ***"
            )

    else:
        recommendation.append(
            f"*** No additional space required for {min_days} of storaged data ***"
        )

    return "\n".join(recommendation)


def run(url, username, password, port, numberofdays, company):

    try:
        numberofdays = int(numberofdays)
    except ValueError:
        raise gr.Error(f"Invalid value for Min days: {numberofdays}")

    with create_manager_session(
        url=url,
        username=username,
        password=password,
        port=int(port),
    ) as session:

        indexes = get_estimate_from_live_vmanage(session)
        stats_db_config = get_stats_db_configuration(session)

    destination_file = f"data/{company}_db_estimator_report.csv"
    filtered_stats_config = filter_config_from_indexes(indexes, stats_db_config)
    headers = parse_headers_from_indexes(indexes)
    data_table = parse_data_table(indexes, filtered_stats_config)
    current_state = create_current_state_message(stats_db_config)
    recommendation = create_recommendation(numberofdays, stats_db_config, data_table)
    unused_indexes_message = parse_unused_available_space(stats_db_config)

    report_file = save_data_to_csv(destination_file, headers, data_table)

    return current_state, recommendation, unused_indexes_message, report_file


if __name__ == "__main__":

    app = gr.Interface(
        fn=run,
        inputs=[
            gr.Textbox(label="vManage Host: ", value=os.getenv("VMANAGE_IP", "")),
            gr.Textbox(label="Username: ", value=os.getenv("VMANAGE_USER", "")),
            gr.Textbox(
                label="Password: ",
                type="password",
                value=os.getenv("VMANAGE_PASSWORD", ""),
            ),
            gr.Textbox(label="Port: ", value=os.getenv("VMANAGE_PORT", "")),
            gr.Textbox(
                label="Min days [1, 7, 14, 30, 90, 180, 365]: ",
                value=os.getenv("DB_MIN_DAYS", "7"),
            ),
            gr.Textbox(label="Company Name: ", value=os.getenv("COMPANY_NAME", "")),
        ],
        outputs=[
            gr.Textbox(label="Current State: "),
            gr.Textbox(label="Recommended Index Updates: "),
            gr.Textbox(label="Unused Indexes: "),
            gr.File(label="CSV Report"),
        ],
        title="vManage Data Base size Estimator",
    )

    app.launch(show_error=True)
